"""Comprehensive validation pack for the Project Manager agent.

Validates the project-manager agent definition:

  1.  meta.json schema & required-field correctness
  2.  Tool-allowance security: read-only on code, no project-state writes,
      no instance/dispatch/control tools (CRITICAL — project-manager is v1
      strategic oversight and must NEVER mutate code, plans, or DB state).
  3.  Auto-discovery via ``AgentRegistry`` + ``SKIP_DIRS`` exclusion.
  4.  Convention compliance: prompt-file completeness, cardinal-rule count,
      first-person voice, no provenance markers, four-flow workflow,
      tool-justification table.
  5.  Prompt composition: system-prompt assembly smoke test, tone/voice
      directive, read-only constraint language.

Modelled after ``tests/unit/test_docwriter_agent_validation.py`` and
``tests/unit/test_reviewer_v2_agent.py``. All tests are pure file +
registry parsing — no daemon/DB startup, no LLM calls.
"""

from __future__ import annotations

import json
import re
from importlib import import_module
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Path constants — resolve against the REAL agents/ dir at repo root.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PM_AGENT_DIR = PROJECT_ROOT / "agents" / "project-manager"
META_PATH = PM_AGENT_DIR / "meta.json"
SOUL_PATH = PM_AGENT_DIR / "soul.md"
RULE_PATH = PM_AGENT_DIR / "rule.md"
WORKFLOW_PATH = PM_AGENT_DIR / "workflow.md"
TOOLS_NOTE_PATH = PM_AGENT_DIR / "tools_note.md"

PROMPT_FILES: tuple[Path, ...] = (
    SOUL_PATH,
    RULE_PATH,
    WORKFLOW_PATH,
    TOOLS_NOTE_PATH,
)


# Forbidden system/implementation tokens that MUST NOT appear in agent
# prompt files. Word-boundary aware. These leak architecture details that
# are supposed to be invisible to the agent (per
# docs/agent-prompt-writing-guide.md).
FORBIDDEN_TOKENS: tuple[str, ...] = (
    "meta.json",
    "tools.allow",
    "tools.deny",
    "daemon/",
    "_tool_registry",
    "skill-set.yaml",
    "agent_id=",
    "seed_all",
    "innate_skills",
    "default_agent_versions",
)

# Forbidden provenance markers (TODO, FIXME, etc.) — agents should never
# ship WIP markers in their prompt files.
PROVENANCE_MARKERS: tuple[str, ...] = (
    "TODO",
    "FIXME",
    "HACK",
    "DRAFT",
    "PLACEHOLDER",
    "XXX",
)


# Tools produced by per-instance factory functions
# (daemon/tools/project.py, project_history.py, critical_notes.py,
# todo_tools.py). These are NOT registered in ``_tool_metadata`` at
# module-import time, so we maintain an explicit allow-list here.
FACTORY_TOOL_NAMES: frozenset[str] = frozenset({
    # project.py -> create_project_tools
    "project_create",
    "project_get",
    "project_list",
    "project_search",
    "project_get_by_instance",
    "project_get_by_directory",
    "project_update",
    "project_set_status",
    "project_add_directory",
    "project_remove_directory",
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
    "project_delete",
    # project_history.py -> create_project_history_tools
    "project_history_add",
    "project_history_list",
    "project_history_search",
    "project_history_delete",
    # critical_notes.py -> create_critical_notes_tools
    "project_cn_add",
    "project_cn_list",
    "project_cn_remove",
    # todo_tools.py -> create_todo_tools
    "todo_list_create",
    "todo_list_update",
    "todo_graph_create",
    "todo_graph_update",
    "todo_graph_add_edge",
    "todo_graph_remove_edge",
    "todo_graph_add_subtask",
    "todo_graph_update_subtask",
    "todo_graph_remove_subtask",
    "todo_view",
    "todo_clear",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_meta() -> dict:
    """Load and return project-manager/meta.json as a dict."""
    with open(META_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _read(path: Path) -> str:
    """Read a UTF-8 text file."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_cardinal_section(rule_text: str) -> str:
    """Extract the text under the '## Cardinal Rules' heading.

    Returns the slice from that heading until the next '##' heading
    (exclusive) or end of file. If no cardinal section is found,
    returns an empty string.
    """
    match = re.search(
        r"^##\s+Cardinal Rules[^\n]*\n(?P<body>.*?)(?=^##\s|\Z)",
        rule_text,
        flags=re.MULTILINE | re.DOTALL,
    )
    return match.group("body").strip() if match else ""


def _force_register_all_tool_modules() -> set[str]:
    """Import every category module so individual tools register themselves.

    Returns the union of known category names (``CATEGORY_MODULES`` keys),
    dynamic-factory tool names (``DYNAMIC_TOOL_NAMES``), and all
    individual tools that registered themselves at import time
    (``_tool_metadata`` keys).
    """
    # Imported lazily to keep top-level test-import surface small.
    from daemon.tools._tool_registry import (  # noqa: WPS433
        CATEGORY_MODULES,
        DYNAMIC_TOOL_NAMES,
        _tool_metadata,
    )

    for entry in CATEGORY_MODULES.values():
        paths = entry if isinstance(entry, list) else [entry]
        for module_path in paths:
            try:
                import_module(module_path)
            except ImportError:
                # Module may not be available in all test environments;
                # the test that consumes this set only cares that valid
                # names pass, not that every module loads.
                continue

    return (
        set(CATEGORY_MODULES.keys())
        | set(DYNAMIC_TOOL_NAMES)
        | FACTORY_TOOL_NAMES
        | set(_tool_metadata.keys())
    )


# =============================================================================
# 1. meta.json schema & configuration
# =============================================================================


class TestMetaJsonSchema:
    """meta.json is valid JSON and exposes the schema the project requires."""

    def test_meta_json_exists(self) -> None:
        """meta.json must exist on disk at the canonical location."""
        assert META_PATH.exists(), f"meta.json not found at {META_PATH}"

    def test_meta_json_is_valid_json(self) -> None:
        """meta.json must parse as a JSON object."""
        meta = _load_meta()
        assert isinstance(meta, dict), (
            f"meta.json root should be a JSON object, got {type(meta).__name__}"
        )

    def test_required_fields_exist(self) -> None:
        """All required top-level fields must be present."""
        meta = _load_meta()
        required = [
            "id",
            "name",
            "description",
            "icon",
            "color",
            "version",
            "innate_skills",
            "skill_injection",
            "tools",
            "team_members",
            "tags",
        ]
        missing = [field for field in required if field not in meta]
        assert not missing, f"Required fields missing from meta.json: {missing}"

    def test_tools_has_allow_and_deny_lists(self) -> None:
        """tools.allow and tools.deny must both be lists."""
        meta = _load_meta()
        tools = meta.get("tools", {})
        assert isinstance(tools.get("allow"), list), (
            f"tools.allow must be a list, got {type(tools.get('allow')).__name__}"
        )
        assert isinstance(tools.get("deny"), list), (
            f"tools.deny must be a list, got {type(tools.get('deny')).__name__}"
        )

    def test_agent_id(self) -> None:
        """meta.json id must be 'project-manager'."""
        meta = _load_meta()
        assert meta["id"] == "project-manager", (
            f"Expected id 'project-manager', got '{meta.get('id')}'"
        )

    def test_agent_name(self) -> None:
        """meta.json name must be 'Project Manager'."""
        meta = _load_meta()
        assert meta["name"] == "Project Manager", (
            f"Expected name 'Project Manager', got '{meta.get('name')!r}'"
        )

    def test_agent_version_is_semver_string(self) -> None:
        """version must be a non-empty string (semver-shaped)."""
        meta = _load_meta()
        version = meta.get("version")
        assert isinstance(version, str) and version, (
            f"version must be a non-empty string, got {version!r}"
        )
        assert re.match(r"^\d+\.\d+\.\d+", version), (
            f"version should look like semver (X.Y.Z...), got {version!r}"
        )

    def test_team_members_is_leader_and_worker(self) -> None:
        """team_members must be ['leader', 'worker'] — v2 delegates to leader + worker."""
        meta = _load_meta()
        assert meta["team_members"] == ["leader", "worker"], (
            f"project-manager must have team_members=['leader', 'worker'] "
            f"(v2 delegation: leader for software, worker for sync). "
            f"Got: {meta['team_members']}"
        )

    def test_skill_injection_is_false(self) -> None:
        """skill_injection must be False — v1 has no skills."""
        meta = _load_meta()
        assert meta["skill_injection"] is False, (
            f"skill_injection must be False (v1 has no skills). "
            f"Got: {meta['skill_injection']!r}"
        )

    def test_innate_skills_is_empty(self) -> None:
        """innate_skills must be [] — v1 ships with no innate skills."""
        meta = _load_meta()
        assert meta["innate_skills"] == [], (
            f"innate_skills must be empty (v1 ships with none). "
            f"Got: {meta['innate_skills']}"
        )

    def test_context_injection_configured(self) -> None:
        """context_injection must enable heuristic shared-md matching."""
        meta = _load_meta()
        ci = meta.get("context_injection")
        assert isinstance(ci, dict), (
            f"context_injection must be a dict (object form), got {type(ci).__name__}"
        )
        assert ci.get("heuristic_match_shared_md_files") is True, (
            f"context_injection.heuristic_match_shared_md_files must be True. "
            f"Got: {ci}"
        )

    def test_no_force_explore_is_true(self) -> None:
        """no_force_explore must be True — project-manager is a strategic agent."""
        meta = _load_meta()
        assert meta.get("no_force_explore") is True, (
            f"no_force_explore should be True for a strategic agent. "
            f"Got: {meta.get('no_force_explore')!r}"
        )

    def test_tags_are_strings(self) -> None:
        """All tags must be non-empty strings."""
        meta = _load_meta()
        tags = meta.get("tags", [])
        assert isinstance(tags, list), f"tags must be a list, got {type(tags).__name__}"
        for tag in tags:
            assert isinstance(tag, str) and tag, (
                f"each tag must be a non-empty string, got {tag!r}"
            )

    def test_agent_id_matches_directory(self) -> None:
        """meta.json id must equal the directory name 'project-manager'."""
        assert PM_AGENT_DIR.name == "project-manager", (
            f"agent directory should be 'project-manager', got '{PM_AGENT_DIR.name}'"
        )
        meta = _load_meta()
        assert meta["id"] == PM_AGENT_DIR.name, (
            f"meta.json id '{meta['id']}' must match directory name "
            f"'{PM_AGENT_DIR.name}'"
        )


# =============================================================================
# 2. Tool-allowance security (CRITICAL — read-only / non-dispatching contract)
# =============================================================================


class TestToolAllowanceSecurity:
    """Verify project-manager's tools config enforces its read-only contract."""

    # Tools that mutate source, plans, project state, instances, or
    # dispatch work. If any of these show up in tools.allow, the agent
    # can violate Cardinal Rule 1 (read-only) or Cardinal Rule 2
    # (no work dispatch).
    KNOWN_WRITE_TOOLS: frozenset[str] = frozenset({
        # File mutations
        "edit_file",
        "write_file",
        "bash",
        # Work dispatch / instance control
        "self",
        "spawn_instance",
        "terminate_instance",
        "send_message",
        # Project / knowledge state mutations
        "experience",
        "project_create",
        "project_delete",
        "project_update",
        "project_set_status",
        "project_cn_add",
        "project_cn_remove",
        "project_history_add",
        "project_history_delete",
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
        # System-internal mechanisms we deny by default in v2
        "mcp",
        "question",
    })

    def test_no_write_tool_in_allow(self) -> None:
        """tools.allow must not contain any write-capable tool."""
        meta = _load_meta()
        allow = set(meta.get("tools", {}).get("allow", []))
        leaks = allow & self.KNOWN_WRITE_TOOLS
        assert not leaks, (
            f"tools.allow must NOT contain write/dispatch tools. "
            f"Leaked: {sorted(leaks)}"
        )

    def test_deny_covers_write_tools(self) -> None:
        """tools.deny must explicitly block every file/instance/write tool."""
        meta = _load_meta()
        deny = set(meta.get("tools", {}).get("deny", []))
        expected = {
            "edit_file",
            "write_file",
            "bash",
            "terminate_instance",
            "experience",
            "self",
            "mcp",
            "question",
        }
        missing = expected - deny
        assert not missing, (
            f"tools.deny must explicitly block {sorted(missing)}. "
            f"Got deny: {sorted(deny)}"
        )

    def test_deny_covers_project_write_tools(self) -> None:
        """tools.deny must explicitly block every project-state write tool."""
        meta = _load_meta()
        deny = set(meta.get("tools", {}).get("deny", []))
        expected = {
            "project_create",
            "project_delete",
            "project_update",
            "project_set_status",
            "project_cn_add",
            "project_cn_remove",
            "project_history_add",
            "project_history_delete",
        }
        missing = expected - deny
        assert not missing, (
            f"tools.deny must explicitly block project-state writes "
            f"{sorted(missing)}. Got deny: {sorted(deny)}"
        )

    def test_allow_deny_no_overlap(self) -> None:
        """tools.allow and tools.deny must be disjoint."""
        meta = _load_meta()
        allow = set(meta.get("tools", {}).get("allow", []))
        deny = set(meta.get("tools", {}).get("deny", []))
        overlap = allow & deny
        assert not overlap, (
            f"tools.allow and tools.deny must NOT overlap. Overlap: {sorted(overlap)}"
        )

    def test_readonly_tools_present_in_allow(self) -> None:
        """All expected read-only / observability tools must be in allow."""
        meta = _load_meta()
        allow = set(meta.get("tools", {}).get("allow", []))
        expected = {
            "project_get",
            "project_list",
            "project_search",
            "explore",
            "project_cn_list",
            "project_history_list",
        }
        missing = expected - allow
        assert not missing, (
            f"Read-only tools missing from allow: {sorted(missing)}. "
            f"Got allow: {sorted(allow)}"
        )

    def test_all_allow_entries_resolve(self) -> None:
        """Every tools.allow entry must be a known category or tool name."""
        meta = _load_meta()
        allow_list = meta.get("tools", {}).get("allow", [])

        valid_names = _force_register_all_tool_modules()
        unresolved: list[str] = [
            entry for entry in allow_list if entry not in valid_names
        ]

        assert not unresolved, (
            f"These tools.allow entries do not resolve to a known category "
            f"or registered tool: {unresolved}. "
            f"Valid (sample): {sorted(valid_names)[:20]}..."
        )

    def test_no_known_categories_in_allow_that_dont_exist(self) -> None:
        """Any category-looking entry in allow must exist in CATEGORY_MODULES.

        Individual tool names are also fine; this test specifically
        guards against typos in category names like ``bashhh``.
        """
        from daemon.tools._tool_registry import CATEGORY_MODULES  # noqa: WPS433

        meta = _load_meta()
        allow_list = meta.get("tools", {}).get("allow", [])

        # Force-import to populate _tool_metadata; then any name that
        # IS a registered individual tool is fine, anything else must
        # be a known category.
        valid_names = _force_register_all_tool_modules()
        bad_categories: list[str] = []
        for entry in allow_list:
            if entry in valid_names:
                continue  # either a category OR a registered individual tool
            # If it looks like a known category but isn't registered,
            # it's a typo (e.g. 'bashh' instead of 'bash').
            if entry in CATEGORY_MODULES:
                bad_categories.append(entry)

        assert not bad_categories, (
            f"Category names in tools.allow that don't resolve: "
            f"{sorted(bad_categories)}. Known categories: "
            f"{sorted(CATEGORY_MODULES.keys())}"
        )

    # -----------------------------------------------------------------
    # CR-1: deny_spawn field — blocks charter/image-reader spawn but
    # keeps chart/image tool access.
    #
    # The contract: ``tools.deny_spawn`` strips the category's backing
    # agent(s) from the spawn-authorization allow-set (driven by
    # ``_check_team_membership`` in ``daemon/tools/_auth.py``) without
    # removing the category from the agent's callable tools. PM uses
    # this so it can call ``chart``/``image`` (which spawn
    # ``charter``/``image-reader`` internally via
    # ``invoke_agent_and_wait``) but cannot spawn them directly via
    # ``spawn_instance``.
    # -----------------------------------------------------------------

    def test_deny_spawn_field_present_in_meta_json(self) -> None:
        """``tools.deny_spawn`` must exist in meta.json as a list.

        Without the field, ``ToolFilter.deny_spawn`` defaults to
        ``None`` and the spawn-gate silently behaves as if the agent
        had no spawn restriction — exactly the v1 behavior CR-1 fixed.
        """
        meta = _load_meta()
        deny_spawn = meta.get("tools", {}).get("deny_spawn")
        assert isinstance(deny_spawn, list), (
            f"meta.json must declare tools.deny_spawn as a list; "
            f"got: {type(deny_spawn).__name__} value={deny_spawn!r}"
        )

    def test_deny_spawn_contains_chart_and_image(self) -> None:
        """``tools.deny_spawn`` must list ``chart`` and ``image``.

        These are the two agent-backed categories PM must NOT
        auto-spawn directly: ``chart`` → ``charter``,
        ``image`` → ``image-reader``. The list is the source of
        truth for the spawn-block contract.
        """
        meta = _load_meta()
        deny_spawn = set(meta.get("tools", {}).get("deny_spawn", []))
        assert {"chart", "image"}.issubset(deny_spawn), (
            f"deny_spawn must include 'chart' and 'image' to block "
            f"charter/image-reader auto-spawn. Got: {sorted(deny_spawn)}"
        )

    def test_deny_spawn_keeps_chart_and_image_in_allow(self) -> None:
        """``chart`` and ``image`` must remain in ``tools.allow``.

        The whole point of ``deny_spawn`` (vs. plain ``deny``) is to
        block the SPAWN while preserving TOOL ACCESS. If PM loses
        ``chart``/``image`` from ``allow``, it can no longer render
        any chart or image — which is a regression. The deny list is
        the agent's job; ``deny_spawn`` is the spawn gate's job.
        """
        meta = _load_meta()
        allow = set(meta.get("tools", {}).get("allow", []))
        assert "chart" in allow, (
            f"'chart' must remain in tools.allow — deny_spawn must "
            f"NOT remove the tool. Got allow: {sorted(allow)}"
        )
        assert "image" in allow, (
            f"'image' must remain in tools.allow — deny_spawn must "
            f"NOT remove the tool. Got allow: {sorted(allow)}"
        )

    def test_deny_spawn_distinct_from_deny(self) -> None:
        """``deny_spawn`` and ``deny`` must be disjoint.

        Same category in both lists is a no-op for the spawn gate
        (``deny`` already strips the agent), so duplicating the entry
        in ``deny_spawn`` is misleading. ``deny_spawn`` is reserved
        for the "block spawn only" cases; everything else belongs in
        ``deny``.
        """
        meta = _load_meta()
        deny = set(meta.get("tools", {}).get("deny", []))
        deny_spawn = set(meta.get("tools", {}).get("deny_spawn", []))
        overlap = deny & deny_spawn
        assert not overlap, (
            f"deny_spawn and deny must be disjoint. Overlap "
            f"(deny already covers this; remove from deny_spawn): "
            f"{sorted(overlap)}"
        )

    def test_toolfilter_model_accepts_deny_spawn(self) -> None:
        """Pydantic ``ToolFilter`` must accept ``deny_spawn`` and
        preserve it on round-trip (registry schema contract).

        This pins the model contract: a ``ToolFilter`` loaded from
        the project's meta.json keeps the field. Without this, a
        future Pydantic migration or a schema rename would silently
        drop the field and the spawn-gate would behave as if PM had
        no ``deny_spawn`` at all — re-introducing the v1 spawn bypass.
        """
        from daemon.registry import ToolFilter  # noqa: WPS433

        meta = _load_meta()
        deny_spawn_raw = meta.get("tools", {}).get("deny_spawn")
        if deny_spawn_raw is None:
            pytest.fail(
                "meta.json must declare tools.deny_spawn before this "
                "model-roundtrip test can run; earlier test should "
                "have caught this."
            )

        tools_obj = meta.get("tools", {})
        tf = ToolFilter(
            allow=tools_obj.get("allow"),
            deny=tools_obj.get("deny"),
            deny_spawn=deny_spawn_raw,
        )
        assert tf.deny_spawn == list(deny_spawn_raw), (
            f"ToolFilter.deny_spawn must preserve the input list. "
            f"Got: {tf.deny_spawn!r} (expected {list(deny_spawn_raw)!r})"
        )


# =============================================================================
# 3. Agent discovery + registry
# =============================================================================


class TestAgentDiscovery:
    """AgentRegistry discovers project-manager and surfaces its metadata."""

    def test_pm_not_in_skip_dirs(self) -> None:
        """'project-manager' must NOT be in SKIP_DIRS."""
        from daemon.registry import SKIP_DIRS  # noqa: WPS433

        assert "project-manager" not in SKIP_DIRS, (
            "'project-manager' should NOT be in SKIP_DIRS — it is a real agent"
        )

    def test_registry_discovers_pm(self) -> None:
        """AgentRegistry.discover() must find the project-manager directory."""
        from daemon.registry import AgentRegistry  # noqa: WPS433

        agents_dir = PM_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        assert registry.exists("project-manager"), (
            "'project-manager' should be discoverable via AgentRegistry"
        )

    def test_pm_in_agent_list(self) -> None:
        """'project-manager' must appear in registry.list_all()."""
        from daemon.registry import AgentRegistry  # noqa: WPS433

        agents_dir = PM_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        agent_ids = {a.id for a in registry.list_all()}
        assert "project-manager" in agent_ids, (
            f"registry.list_all() must include 'project-manager'. "
            f"Got {len(agent_ids)} agents."
        )

    def test_registry_metadata_fields(self) -> None:
        """registry.get('project-manager') must surface id/name/version."""
        from daemon.registry import AgentRegistry  # noqa: WPS433

        agents_dir = PM_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        md = registry.get("project-manager")
        assert md is not None, "registry.get('project-manager') returned None"
        assert md.id == "project-manager", f"id mismatch: {md.id}"
        assert md.name == "Project Manager", f"name mismatch: {md.name!r}"
        assert md.version == "2.0.0", f"version mismatch: {md.version!r}"
        assert md.team_members == ["leader", "worker"], (
            f"team_members must be ['leader', 'worker'] (v2 delegation). "
            f"Got: {md.team_members}"
        )

    def test_meta_conforms_to_agent_metadata_model(self) -> None:
        """AgentMetadata.model_validate(meta_with_path) must succeed."""
        from daemon.registry import AgentMetadata  # noqa: WPS433

        meta = _load_meta()
        # ``path`` is injected by discover(); meta.json doesn't carry it.
        meta["path"] = str(PM_AGENT_DIR)

        md = AgentMetadata.model_validate(meta)  # must not raise
        assert md.id == "project-manager"
        assert md.name == "Project Manager"
        assert md.team_members == ["leader", "worker"]


# =============================================================================
# 4. Convention compliance — prompt-file shape
# =============================================================================


class TestConventionCompliance:
    """Prompt files comply with the project's agent-prompt conventions."""

    def test_all_prompt_files_exist(self) -> None:
        """All four canonical prompt files must exist."""
        missing = [str(p) for p in PROMPT_FILES if not p.exists()]
        assert not missing, f"Missing prompt files: {missing}"

    @pytest.mark.parametrize("prompt_path", PROMPT_FILES, ids=lambda p: p.name)
    def test_prompt_file_non_empty(self, prompt_path: Path) -> None:
        """Each prompt file must be non-empty (>= 100 chars of body)."""
        text = _read(prompt_path)
        assert len(text) >= 100, (
            f"{prompt_path.name} is too short ({len(text)} chars)"
        )

    @pytest.mark.parametrize("prompt_path", PROMPT_FILES, ids=lambda p: p.name)
    def test_no_forbidden_system_tokens(self, prompt_path: Path) -> None:
        """No prompt file may reference daemon internals or schema keys."""
        text = _read(prompt_path)

        found: list[tuple[str, int]] = []
        for token in FORBIDDEN_TOKENS:
            # Use plain substring search — tokens contain slashes/dots so
            # word boundaries don't help. Report each occurrence's line.
            for line_no, line in enumerate(text.splitlines(), start=1):
                if token in line:
                    found.append((token, line_no))

        assert not found, (
            f"{prompt_path.name} contains forbidden system tokens: "
            + ", ".join(f"{tok!r}@L{ln}" for tok, ln in found)
        )

    @pytest.mark.parametrize("prompt_path", PROMPT_FILES, ids=lambda p: p.name)
    def test_no_provenance_markers(self, prompt_path: Path) -> None:
        """No prompt file may contain TODO / FIXME / HACK / DRAFT / XXX."""
        text = _read(prompt_path)

        found: list[tuple[str, int]] = []
        for marker in PROVENANCE_MARKERS:
            pattern = re.compile(rf"\b{re.escape(marker)}\b")
            for match in pattern.finditer(text):
                line_no = text[: match.start()].count("\n") + 1
                found.append((marker, line_no))

        assert not found, (
            f"{prompt_path.name} contains provenance markers: "
            + ", ".join(f"{m!r}@L{ln}" for m, ln in found)
        )

    def test_cardinal_count_at_most_seven(self) -> None:
        """rule.md must have at most 7 Cardinal Rules (target == 7)."""
        rule_text = _read(RULE_PATH)
        cardinal_section = _extract_cardinal_section(rule_text)

        # ``\d+.`` at line start (allow leading whitespace before digit).
        numbered = re.findall(
            r"^\s*\d+\.\s", cardinal_section, flags=re.MULTILINE
        )
        assert len(numbered) <= 7, (
            f"Too many Cardinal Rules ({len(numbered)}); guide allows <= 7. "
            f"Found: {numbered}"
        )

    def test_cardinal_count_exactly_seven(self) -> None:
        """rule.md must have exactly 7 Cardinal Rules (current target)."""
        rule_text = _read(RULE_PATH)
        cardinal_section = _extract_cardinal_section(rule_text)

        numbered = re.findall(
            r"^\s*\d+\.\s", cardinal_section, flags=re.MULTILINE
        )
        assert len(numbered) == 7, (
            f"Expected exactly 7 Cardinal Rules, found {len(numbered)}. "
            f"Update this test if Cardinal set changed intentionally."
        )

    def test_first_person_voice_in_soul(self) -> None:
        """soul.md must use first-person voice (I / me / my / myself)."""
        soul_text = _read(SOUL_PATH)

        # Case-insensitive search for any first-person pronoun. soul.md
        # is intentionally terse; "My" (capital) and "I" are the dominant
        # forms, while lowercase "my" may not appear.
        pronouns = re.findall(
            r"\b(I|me|my|mine|myself)\b", soul_text, flags=re.IGNORECASE
        )
        assert len(pronouns) >= 5, (
            f"soul.md must use first-person voice consistently "
            f"(found only {len(pronouns)} pronouns: {pronouns})"
        )

        # Sanity: at least one bare "I " (capital I followed by space) —
        # the canonical first-person marker.
        assert re.search(r"\bI\s", soul_text), (
            "soul.md must contain at least one 'I ' (capital I + verb)"
        )

    def test_workflow_has_four_flows(self) -> None:
        """workflow.md must enumerate the four canonical flows."""
        workflow_text = _read(WORKFLOW_PATH)
        expected = [
            "Risk Assessment",
            "Progress Reporting",
            "Scope Assessment",
            "Decision Framing",
        ]
        for flow in expected:
            assert flow in workflow_text, (
                f"workflow.md must mention flow '{flow}'"
            )

    def test_tool_justification_table_in_tools_note(self) -> None:
        """tools_note.md must contain the | Tool | justification table."""
        tools_text = _read(TOOLS_NOTE_PATH)
        assert "| Tool |" in tools_text, (
            "tools_note.md must contain a tool-justification table "
            "with a '| Tool |' header"
        )

    def test_no_skill_set_yaml_or_skills_template(self) -> None:
        """v1 ships without skills — no skill-set.yaml / skills-template/."""
        skill_set_yaml = PM_AGENT_DIR / "skill-set.yaml"
        skills_template = PM_AGENT_DIR / "skills-template"

        assert not skill_set_yaml.exists(), (
            f"v1 must NOT ship skill-set.yaml. Found at {skill_set_yaml}"
        )
        assert not skills_template.exists(), (
            f"v1 must NOT ship skills-template/. Found at {skills_template}"
        )


# =============================================================================
# 5. Prompt composition — assembly smoke tests
# =============================================================================


class TestPromptComposition:
    """The four prompt files assemble into a coherent system prompt."""

    def test_system_prompt_assembles_without_error(self) -> None:
        """All four files read cleanly; concatenation produces a body."""
        assembled_parts: list[str] = []
        for path in PROMPT_FILES:
            text = _read(path)
            assert text, f"{path.name} is empty"
            assembled_parts.append(text)

        assembled = "\n\n".join(assembled_parts)
        # No assertion on size here — just smoke-test that all four
        # files joined together yield a non-empty string.
        assert assembled, "concatenated prompt body is empty"
        assert len(assembled) >= 500, (
            f"concatenated prompt body is suspiciously short: "
            f"{len(assembled)} chars"
        )

    def test_soul_has_tone_directive(self) -> None:
        """soul.md must declare a Tone or Voice heading."""
        soul_text = _read(SOUL_PATH)
        assert (
            re.search(r"^##\s+Tone\b", soul_text, re.MULTILINE)
            or re.search(r"^##\s+Voice\b", soul_text, re.MULTILINE)
            or "Tone & Voice" in soul_text
        ), "soul.md must include a 'Tone' or 'Voice' heading"

    def test_rule_has_readonly_constraint(self) -> None:
        """rule.md must declare a read-only / never-mutate constraint."""
        rule_text = _read(RULE_PATH)
        has_readonly = "read-only" in rule_text.lower()
        has_never_mutate = (
            "never" in rule_text.lower()
            and "mutate" in rule_text.lower()
        )
        has_never_write = (
            "never" in rule_text.lower()
            and "write" in rule_text.lower()
        )

        assert has_readonly or has_never_mutate or has_never_write, (
            "rule.md must declare a read-only constraint "
            "('read-only' OR ('never' + 'mutate') OR ('never' + 'write'))"
        )

    def test_rule_mentions_no_dispatch(self) -> None:
        """rule.md must explicitly forbid work dispatch."""
        rule_text = _read(RULE_PATH).lower()
        assert "dispatch" in rule_text, (
            "rule.md must mention 'dispatch' (Cardinal #2 — no work dispatch)"
        )

    def test_soul_cites_rule_for_severity_framing(self) -> None:
        """soul.md must hand off severity framing to rule.md (cross-doc link)."""
        soul_text = _read(SOUL_PATH)
        # soul.md says "Per-severity framing (�/🟡/🟢): see `rule.md`"
        assert "rule.md" in soul_text, (
            "soul.md must cross-reference rule.md (e.g., severity framing)"
        )

    def test_workflow_cites_soul_and_rule(self) -> None:
        """workflow.md must reference soul.md templates + rule.md cardinals."""
        workflow_text = _read(WORKFLOW_PATH)
        assert "soul.md" in workflow_text, (
            "workflow.md must reference soul.md for output templates"
        )
        assert "rule.md" in workflow_text, (
            "workflow.md must reference rule.md for Cardinal Rules"
        )
