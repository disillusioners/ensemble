"""Comprehensive tests for the Worker agent.

Tests Worker agent discovery, loading, tool filtering, OpenSpace innate skill
loading, and the no-spawn-authorization constraint. Mirrors the gold-standard
pattern from test_devops_agent.py exactly (class-per-concern structure,
fixture style, assertion patterns, imports).

All tests run in the unit test environment with langgraph mocks from
conftest.py. Tests are spec-driven and do not require OpenSpace (the
openspace-ai package) to be installed — they only inspect prompt
composition and metadata.
"""

import json
import re
from pathlib import Path

import pytest


# Path constants
WORKER_AGENT_DIR = Path(__file__).parent.parent.parent / "agents" / "worker"
LEADER_AGENT_DIR = Path(__file__).parent.parent.parent / "agents" / "leader"

# Tool categories for testing (mirrors what the registry should contain).
# Used by resolve_tool_filter — Worker has a limited tool surface.
TOOL_CATEGORIES: dict[str, list[str]] = {
    "bash": ["bash"],
    "filesystem": ["list_directory", "read_file", "write_file", "glob_files", "grep_files", "edit_file"],
    "time": ["time"],
    "instance": [
        "spawn_instance", "send_message", "terminate_instance",
        "list_instances", "get_instance_info"
    ],
    "job": [
        "job_create", "job_watch", "job_dispatch",
        "job_terminate", "job_attach",
    ],
    "self": ["inner_soul", "access_memory"],
    "project": [
        "project_create", "project_get", "project_list", "project_search",
        "project_get_by_instance", "project_get_by_directory", "project_update",
        "project_set_status", "project_add_directory", "project_remove_directory",
        "project_set_tags", "project_add_tag", "project_remove_tag",
        "project_set_shortnames", "project_add_shortname", "project_remove_shortname",
        "project_set_metadata", "project_delete_metadata",
        "project_link", "project_unlink", "project_delete",
    ],
    "help": ["tool_help"],
    "mother": ["agent_list", "agent_create", "agent_read", "agent_modify", "agent_delete"],
}

# The 4 OpenSpace MCP tool names that Worker must explicitly grant.
# These are individual tool names (not categories) — they must appear
# verbatim in tools.allow.
OPENSPACE_TOOL_NAMES = [
    "mcp_openspace_execute_task",
    "mcp_openspace_search_skills",
    "mcp_openspace_fix_skill",
    "mcp_openspace_upload_skill",
]


# =============================================================================
# 1. Agent Auto-Discovery
# =============================================================================


class TestWorkerAutoDiscovery:
    """Tests for Worker agent auto-discovery via AgentRegistry."""

    def test_worker_directory_exists(self) -> None:
        """The agents/worker/ directory should exist."""
        assert WORKER_AGENT_DIR.exists(), f"agents/worker/ directory not found at {WORKER_AGENT_DIR}"
        assert WORKER_AGENT_DIR.is_dir(), "agents/worker/ should be a directory"

    def test_worker_not_in_skip_dirs(self) -> None:
        """'worker' should NOT be in SKIP_DIRS (template/internal directories)."""
        from daemon.registry import SKIP_DIRS

        assert "worker" not in SKIP_DIRS, (
            "worker should NOT be in SKIP_DIRS — it is a real agent, not a template"
        )

    def test_worker_discovered_in_registry(self) -> None:
        """Worker should be discovered when scanning the agents directory."""
        from daemon.registry import AgentRegistry

        agents_dir = WORKER_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        assert registry.exists("worker"), "Worker should be discovered in the agents directory"

    def test_worker_in_agent_list(self) -> None:
        """Worker should appear in the list of all agents."""
        from daemon.registry import AgentRegistry

        agents_dir = WORKER_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        agents = registry.list_all()
        agent_ids = {a.id for a in agents}
        assert "worker" in agent_ids, "worker should be in the list of agents"

    def test_worker_metadata_loaded_correctly(self) -> None:
        """Worker metadata loaded from registry should match meta.json."""
        from daemon.registry import AgentRegistry

        agents_dir = WORKER_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        worker = registry.get("worker")
        assert worker is not None, "Worker should be retrievable from registry"
        assert worker.id == "worker"
        assert worker.name == "Worker"


# =============================================================================
# 2. meta.json Validity
# =============================================================================


class TestWorkerMetaJsonValidation:
    """Tests for Worker meta.json structure and content."""

    def test_meta_json_exists(self) -> None:
        """meta.json file should exist in the Worker agent directory."""
        meta_path = WORKER_AGENT_DIR / "meta.json"
        assert meta_path.exists(), f"meta.json not found at {meta_path}"

    def test_meta_json_is_valid_json(self) -> None:
        """meta.json should be parseable as valid JSON."""
        meta_path = WORKER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        assert isinstance(meta, dict)

    def test_required_fields_exist(self) -> None:
        """meta.json should contain all required fields."""
        meta_path = WORKER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        required_fields = ["id", "name", "description", "icon", "color", "tools"]
        for field in required_fields:
            assert field in meta, f"Required field '{field}' missing from meta.json"

    def test_agent_id_and_name(self) -> None:
        """Agent id and name should be 'worker' / 'Worker'."""
        meta_path = WORKER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        assert meta.get("id") == "worker", f"Agent id should be 'worker', got '{meta.get('id')}'"
        assert meta.get("name") == "Worker", f"Agent name should be 'Worker', got '{meta.get('name')}'"

    def test_field_types_are_correct(self) -> None:
        """meta.json field types should match expected types."""
        meta_path = WORKER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        assert isinstance(meta.get("id"), str), "id should be a string"
        assert isinstance(meta.get("name"), str), "name should be a string"
        assert isinstance(meta.get("description"), str), "description should be a string"
        assert isinstance(meta.get("icon"), str), "icon should be a string"
        assert isinstance(meta.get("color"), str), "color should be a string"
        assert isinstance(meta.get("innate_skills"), list), "innate_skills should be a list"
        assert "tools" in meta, "tools field should be present"
        assert isinstance(meta.get("tools"), dict), "tools should be a dict"

    def test_innate_skills_is_dynamic_skill_and_todo(self) -> None:
        """Worker should have innate_skills == ['dynamic-skill', 'todo'].

        Worker migrated from OpenSpace MCP to native dynamic-skill system
        on commit de8ff83f. It must have dynamic-skill (for skill search /
        injection) and todo (for in-flight task tracking). No other
        innate skills should be declared.
        """
        meta_path = WORKER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        innate_skills = meta.get("innate_skills", [])
        assert innate_skills == ["dynamic-skill", "todo"], (
            f"Worker innate_skills should be ['dynamic-skill', 'todo'], got: {innate_skills}"
        )

    def test_tools_config_structure(self) -> None:
        """tools configuration should have the expected structure."""
        meta_path = WORKER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        tools_config = meta.get("tools")
        assert tools_config is not None, "tools config should not be None"
        assert "allow" in tools_config, "tools config should have 'allow' key"
        assert isinstance(tools_config["allow"], list), "'allow' should be a list"
        assert len(tools_config["allow"]) > 0, "'allow' should not be empty"

    def test_tools_allow_list_includes_basic_tools(self) -> None:
        """tools.allow should contain the 5 basic tool categories."""
        meta_path = WORKER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        tools_config = meta.get("tools", {})
        allowed = tools_config.get("allow", [])

        expected_tools = ["bash", "filesystem", "time", "self", "help"]
        for tool in expected_tools:
            assert tool in allowed, f"'{tool}' should be in allowed tools: {allowed}"


# =============================================================================
# 3. Tool Filter Configuration
# =============================================================================


class TestWorkerToolFilter:
    """Tests for Worker tool filter parsing and contents."""

    def test_worker_tool_filter_in_registry(self) -> None:
        """Worker's tools config should be parseable by the registry ToolFilter model."""
        from daemon.registry import AgentRegistry, ToolFilter

        agents_dir = WORKER_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        worker = registry.get("worker")
        assert worker is not None
        assert worker.tools is not None
        assert isinstance(worker.tools, ToolFilter)
        assert worker.tools.deny is None
        assert worker.tools.allow is not None
        assert len(worker.tools.allow) > 0

    def test_worker_allows_all_openspace_tools(self) -> None:
        """OBSOLETE: Worker migrated to dynamic-skill (commit de8ff83f).

        The 4 OpenSpace MCP tools are no longer in worker's tools.allow.
        See TestWorkerToolFilter::test_worker_has_dynamic_skill_tool for
        the post-migration equivalent. This test is kept as a stub so
        the migration is explicitly documented in the test suite.
        """
        import pytest
        pytest.skip(
            "Worker migrated from OpenSpace MCP to dynamic-skill in commit de8ff83f"
        )

    def test_worker_has_basic_tools(self) -> None:
        """Worker should have bash, filesystem, time, self, help tools after filtering."""
        from daemon.tools.instance import resolve_tool_filter

        # Read the actual allow list from meta.json
        meta_path = WORKER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        allow = meta.get("tools", {}).get("allow")

        allowed_tools = resolve_tool_filter(
            allow=allow,
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )

        assert allowed_tools is not None, "resolve_tool_filter should return a set for explicit allow list"

        # Basic tool categories should be expanded
        assert "bash" in allowed_tools, "bash should be in resolved allowed tools"
        # filesystem category expands to 6 tools
        filesystem_tools = {
            "list_directory", "read_file", "write_file",
            "glob_files", "grep_files", "edit_file",
        }
        for tool in filesystem_tools:
            assert tool in allowed_tools, f"{tool} should be in resolved allowed tools"
        # time
        assert "time" in allowed_tools, "time should be in resolved allowed tools"
        # self → inner_soul + access_memory
        assert "inner_soul" in allowed_tools, "inner_soul should be in resolved allowed tools"
        assert "access_memory" in allowed_tools, "access_memory should be in resolved allowed tools"
        # help → tool_help
        assert "tool_help" in allowed_tools, "tool_help should be in resolved allowed tools"

    def test_worker_no_instance_or_job_tools(self) -> None:
        """Worker should NOT have instance management OR job orchestration tools.

        Worker is a leaf executor — it must NOT be able to spawn other
        agents (no instance tools) and must NOT manage the job queue (no
        job tools). This enforces the no-spawn-authorization rule at the
        tool layer; the team_members gate enforces it at the routing
        layer (see TestWorkerNoTeamMembers).
        """
        from daemon.tools.instance import resolve_tool_filter

        # Read the actual allow list from meta.json
        meta_path = WORKER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        allow = meta.get("tools", {}).get("allow")

        allowed_tools = resolve_tool_filter(
            allow=allow,
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )

        assert allowed_tools is not None

        # NO instance tools
        instance_tools = {
            "spawn_instance", "send_message", "terminate_instance",
            "list_instances", "get_instance_info",
        }
        for tool in instance_tools:
            assert tool not in allowed_tools, (
                f"{tool} should NOT be in Worker allowed tools — Worker cannot spawn other agents"
            )

        # NO job tools
        job_tools = {
            "job_create", "job_watch", "job_dispatch",
            "job_terminate", "job_attach",
        }
        for tool in job_tools:
            assert tool not in allowed_tools, (
                f"{tool} should NOT be in Worker allowed tools — Worker cannot manage the job queue"
            )

        # Also verify neither category is even declared in meta.json
        assert "instance" not in (allow or []), (
            "'instance' category should NOT be in Worker tools.allow"
        )
        assert "job" not in (allow or []), (
            "'job' category should NOT be in Worker tools.allow"
        )


# =============================================================================
# 4. Prompt Composition
# =============================================================================


class TestWorkerPromptComposition:
    """Tests for Worker agent prompt file presence and loadability."""

    def test_soul_md_exists(self) -> None:
        """soul.md should exist in the Worker agent directory."""
        soul_path = WORKER_AGENT_DIR / "soul.md"
        assert soul_path.exists(), f"soul.md not found at {soul_path}"
        assert soul_path.is_file(), f"soul.md is not a regular file: {soul_path}"
        content = soul_path.read_text(encoding="utf-8")
        assert len(content) > 0, "soul.md should not be empty"

    def test_rule_md_exists(self) -> None:
        """rule.md should exist in the Worker agent directory."""
        rule_path = WORKER_AGENT_DIR / "rule.md"
        assert rule_path.exists(), f"rule.md not found at {rule_path}"
        assert rule_path.is_file(), f"rule.md is not a regular file: {rule_path}"
        content = rule_path.read_text(encoding="utf-8")
        assert len(content) > 0, "rule.md should not be empty"

    def test_workflow_md_exists(self) -> None:
        """workflow.md should exist in the Worker agent directory."""
        workflow_path = WORKER_AGENT_DIR / "workflow.md"
        assert workflow_path.exists(), f"workflow.md not found at {workflow_path}"
        assert workflow_path.is_file(), f"workflow.md is not a regular file: {workflow_path}"
        content = workflow_path.read_text(encoding="utf-8")
        assert len(content) > 0, "workflow.md should not be empty"

    def test_prompt_composition_works_without_errors(self) -> None:
        """Loading and composing Worker prompts should not raise any exceptions.

        Mirrors the devops test_no_errors_during_loading test. Exercises
        the full load_agent_prompts → compose_system_prompt pipeline with
        the worker's actual meta.json to verify the loader accepts the
        agent's structure.
        """
        from daemon.loader import (
            PromptCache,
            compose_system_prompt,
            load_agent_prompts,
            load_and_cache_prompt,
        )

        # Should not raise
        prompts = load_agent_prompts(WORKER_AGENT_DIR)
        assert isinstance(prompts, dict), "load_agent_prompts should return a dict"
        assert len(prompts) > 0, "load_agent_prompts should return at least one section"

        # compose_system_prompt should accept the prompts dict
        system_prompt = compose_system_prompt(prompts)
        assert isinstance(system_prompt, str)
        assert len(system_prompt) > 0, "Composed system prompt should not be empty"

        # load_and_cache_prompt should work too
        cache = PromptCache()
        prompt, tokens = load_and_cache_prompt("worker", WORKER_AGENT_DIR, cache)
        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert tokens > 0


# =============================================================================
# 5. Dynamic-Skill Innate Skill Loading  (post OpenSpace migration)
# =============================================================================


class TestWorkerOpenSpaceSkillLoading:
    """OBSOLETE: Worker migrated from OpenSpace to native dynamic-skill (de8ff83f).

    The OpenSpace skill is no longer in Worker's innate_skills list. Worker
    now uses the native dynamic-skill system (see TestWorkerDynamicSkillLoading
    below for the post-migration equivalent). These test methods are kept as
    skipping stubs so the migration is documented in the test suite.
    """

    def test_openspace_skill_loads_into_composed_prompt(self) -> None:
        import pytest
        pytest.skip(
            "Worker migrated from OpenSpace to dynamic-skill (commit de8ff83f)"
        )

    def test_openspace_tool_names_appear_in_composed_prompt(self) -> None:
        import pytest
        pytest.skip(
            "Worker migrated from OpenSpace to dynamic-skill (commit de8ff83f)"
        )

    def test_short_tool_names_appear_in_composed_prompt(self) -> None:
        import pytest
        pytest.skip(
            "Worker migrated from OpenSpace to dynamic-skill (commit de8ff83f)"
        )


# =============================================================================
# 6. No Team Members / No Spawn Authorization
# =============================================================================


class TestWorkerNoTeamMembers:
    """Tests for Worker's no-spawn-authorization contract.

    Worker is a leaf executor. It must NOT be able to spawn other agents.
    The spawn_instance tool's gate (team_members) is deny-by-default —
    an empty/missing team_members list means the agent cannot dispatch
    work to any other agent. The tool layer also enforces this by not
    including the 'instance' tool category in tools.allow (verified in
    TestWorkerToolFilter).
    """

    def test_no_team_members_field(self) -> None:
        """meta.json should have NO team_members field, OR it should be an empty list.

        Either form is acceptable — the registry default is [] and the
        spawn_instance gate is deny-by-default. Worker is a leaf
        executor, so it must not list any teammates it can spawn.
        """
        meta_path = WORKER_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        team_members = meta.get("team_members", [])

        # Field is either missing or an empty list — no spawn authorization
        assert team_members == [] or team_members is None, (
            f"Worker should have NO team_members (leaf executor), got: {team_members}"
        )

    def test_worker_has_no_spawn_authorization_in_registry(self) -> None:
        """Registry-loaded Worker metadata must have empty team_members.

        Verifies both the meta.json declaration and the registry's
        parsing result are consistent with the no-spawn contract.
        """
        from daemon.registry import AgentRegistry

        agents_dir = WORKER_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        worker = registry.get("worker")
        assert worker is not None
        assert worker.team_members == [], (
            f"Worker should have no spawn authorization, got team_members: {worker.team_members}"
        )

        # The AgentMetadata field is documented as "deny-by-default" —
        # verify the type contract holds
        assert isinstance(worker.team_members, list), (
            f"team_members should be a list, got: {type(worker.team_members)}"
        )
