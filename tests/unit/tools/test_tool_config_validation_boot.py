"""Boot-path tests for tool-config validation against real agent configs.

Companion suite to ``test_frozen_tool_name_discovery.py`` (commit 4f326f8d).
That suite proves the frozen-binary side of the seam: when
``discover_all_tool_names()`` cannot read any category-module source
(PyInstaller bytecode-only build), the static ``KNOWN_TOOL_NAMES`` fallback
keeps factory-created tool names in the validator universe.

This suite proves the OTHER two halves of the same seam, in source mode
against the REAL ``agents/`` tree — no daemon boot required:

1. ``test_source_mode_validation_project_manager_zero_warnings``
   Source-mode regression pin for the 2026-08-20 prod incident: running the
   exact boot validation path (``AgentRegistry.discover()`` +
   ``validate_tool_configs()``, plus the ``get_registry()`` boot wrapper that
   logs each returned warning at WARNING on logger ``daemon.registry``)
   against the real ``agents/project-manager/meta.json`` must produce ZERO
   ``"... is neither a known category nor a known tool"`` occurrences for
   project-manager. Pre-fix, the same path in the frozen binary produced
   30-32 such WARNING lines at every prod boot.

2. ``test_unknown_tool_name_still_warns_in_source_mode``
   No-false-negative guard: a deliberately unknown tool name
   (``totally_bogus_tool_xyz``) added to a COPY of the project-manager
   config (staged under ``tmp_path`` — no repo file is modified) MUST still
   produce the warning through the same boot path. This pins the validation
   feature itself: if a future change made ``validate_tool_configs()`` stop
   warning entirely (e.g. over-broad fallback), test 1 alone would stay
   green while the validator silently rotted.

Environment strategy mirrors ``test_frozen_tool_name_discovery.py``:
``get_registry()`` resolves its agents dir as ``Path(__file__).parent.parent
/ "agents"`` (source mode) or ``Path(sys.executable).parent / "agents"``
(frozen mode). Tests here monkeypatch ``daemon.registry.__file__`` (and
reset the module-level ``_registry`` cache, restored automatically by
monkeypatch) to point the boot path at either the real repo (test 1) or a
``tmp_path`` staged tree (test 2). The daemon is never booted; no ports,
no DB, no network.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

import daemon.registry as dr
from daemon.registry import AgentRegistry

# Repo root is three parents up from this test file:
# tests/unit/tools/test_xxx.py -> parents[3] == repo_root.
REPO_ROOT = Path(__file__).resolve().parents[3]

# The exact boot-log phrase from the 2026-08-20 incident (32 WARNING lines
# for agent project-manager in the frozen prod binary).
PM_WARNING_PHRASE = "is neither a known category nor a known tool"

# Deliberately-unknown tool name used for the no-false-negative guard. It
# matches no category, no tool in KNOWN_TOOL_NAMES, and no DYNAMIC_TOOL_PREFIXES.
BOGUS_TOOL_NAME = "totally_bogus_tool_xyz"


def test_source_mode_validation_project_manager_zero_warnings(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Source-mode boot validation of the REAL project-manager config emits
    ZERO ``is neither a known category nor a known tool`` warnings.

    Two seams are checked for the same behavior:

    * Direct seam — ``AgentRegistry(real agents dir).discover()`` +
      ``validate_tool_configs()``: the returned warning list contains no
      project-manager entry with the phrase (and no project-manager entry
      at all, matching the frozen-mode twin test's contract).
    * Boot seam — ``get_registry()`` (the exact function daemon boot calls;
      manager.py imports it at module scope): with the module-level cache
      reset, it re-runs discovery+validation against the real repo (source
      mode, ``__file__`` untouched) and logs each warning at WARNING on
      logger ``daemon.registry``; zero such records may mention
      project-manager together with the phrase.
    """
    agents_dir = REPO_ROOT / "agents"
    assert agents_dir.is_dir(), f"agents/ not found at {agents_dir}"
    assert (agents_dir / "project-manager").is_dir(), (
        "project-manager agent must exist for this regression test"
    )

    # --- Seam 1: direct validate_tool_configs() against the real config ---
    registry = AgentRegistry(agents_dir)
    registry.discover()
    assert registry.exists("project-manager"), (
        "project-manager was not discovered from the real agents/ tree"
    )

    warnings = registry.validate_tool_configs()
    pm_warnings = [w for w in warnings if "project-manager" in w]
    pm_neither = [w for w in pm_warnings if PM_WARNING_PHRASE in w]

    assert pm_neither == [], (
        f"Expected ZERO '{PM_WARNING_PHRASE}' warnings for project-manager "
        f"in source mode, got {len(pm_neither)}: {pm_neither}"
    )
    assert pm_warnings == [], (
        f"Expected ZERO project-manager validation warnings in source mode, "
        f"got {len(pm_warnings)}: {pm_warnings}"
    )

    # --- Seam 2: get_registry() boot wrapper + WARNING-level logging ---
    # Reset the module-level cache so get_registry() re-runs discovery and
    # validation (boot behavior). monkeypatch restores the prior value even
    # though get_registry() assigns a fresh registry into it.
    monkeypatch.setattr(dr, "_registry", None)

    with caplog.at_level(logging.WARNING, logger="daemon.registry"):
        dr.get_registry()

    boot_pm_neither = [
        r
        for r in caplog.records
        if r.name == "daemon.registry"
        and r.levelno == logging.WARNING
        and "project-manager" in r.getMessage()
        and PM_WARNING_PHRASE in r.getMessage()
    ]
    assert boot_pm_neither == [], (
        f"Boot path get_registry() logged unexpected project-manager "
        f"'{PM_WARNING_PHRASE}' warnings in source mode: "
        f"{[r.getMessage() for r in boot_pm_neither]}"
    )


def test_unknown_tool_name_still_warns_in_source_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A genuinely-unknown tool entry MUST still warn through the boot path
    (no-false-negative guard for the validation feature itself).

    Stages a ``tmp_path`` copy of the real ``agents/project-manager/meta.json``
    with ``totally_bogus_tool_xyz`` appended to ``tools.allow``, then redirects
    ``get_registry()``'s source-mode agents-dir resolution
    (``Path(__file__).parent.parent / "agents"``) at that tmp tree. No repo
    file is modified; the daemon is not booted.

    Asserts the boot WARNING log record exists on logger ``daemon.registry``,
    mentions the bogus name AND the exact phrase — proving the validator still
    catches unknown entries now that the known-universe has a static fallback
    (a fix that silenced ALL warnings would pass test 1 but fail here).
    """
    # Stage: tmp/daemon/registry.py (for __file__ redirect) + tmp/agents/
    # pm-bogus-probe/meta.json (real PM config + one bogus allow entry).
    (tmp_path / "daemon").mkdir()
    staged_agent_dir = tmp_path / "agents" / "pm-bogus-probe"
    staged_agent_dir.mkdir(parents=True)

    real_meta_path = REPO_ROOT / "agents" / "project-manager" / "meta.json"
    meta = json.loads(real_meta_path.read_text(encoding="utf-8"))
    meta["id"] = "pm-bogus-probe"
    meta.setdefault("tools", {}).setdefault("allow", []).append(BOGUS_TOOL_NAME)
    (staged_agent_dir / "meta.json").write_text(
        json.dumps(meta), encoding="utf-8"
    )

    # Redirect the boot path's base-dir resolution and bypass the registry
    # cache. Both restores are automatic via monkeypatch.
    monkeypatch.setattr(
        dr, "__file__", str(tmp_path / "daemon" / "registry.py")
    )
    monkeypatch.setattr(dr, "_registry", None)

    with caplog.at_level(logging.WARNING, logger="daemon.registry"):
        dr.get_registry()

    matching = [
        r
        for r in caplog.records
        if r.name == "daemon.registry"
        and r.levelno == logging.WARNING
        and BOGUS_TOOL_NAME in r.getMessage()
        and PM_WARNING_PHRASE in r.getMessage()
    ]

    assert matching, (
        f"Expected a WARNING on logger 'daemon.registry' containing both "
        f"'{BOGUS_TOOL_NAME}' and '{PM_WARNING_PHRASE}' — the validation "
        f"feature must still warn on genuinely-unknown tool entries. "
        f"Captured daemon.registry records: "
        f"{[r.getMessage() for r in caplog.records if r.name == 'daemon.registry']}"
    )
    # The boot wrapper prefixes every validation warning with this label.
    assert any(
        "Tool config validation" in r.getMessage() for r in matching
    ), (
        f"Expected boot-log 'Tool config validation:' prefix, got: "
        f"{[r.getMessage() for r in matching]}"
    )
