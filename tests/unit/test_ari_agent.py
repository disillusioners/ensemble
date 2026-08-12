"""Comprehensive tests for the Ari agent.

Tests Ari (jober-hybrid) agent discovery, loading, tool filtering, prompt
composition, and the front-door no-spawn-authorization contract. Mirrors
the gold-standard pattern from test_devops_agent.py and the sister Worker
pattern from test_worker_agent.py (class-per-concern structure, fixture
style, assertion patterns, imports).

All tests run in the unit test environment with langgraph mocks from
conftest.py. Tests are spec-driven and validate the actual agent files on
disk — they do not require LLM access or job-queue execution.

Ari is a "jober-hybrid" agent — it has JOB tools (job_create / job_watch /
job_continue) for delegating to Leader and Worker via the job queue, AND
direct execution tools (bash, filesystem) for Mode 1 quick tasks handled
in-process. Crucially, it does NOT have the `instance` tool category — Ari
cannot spawn_instance directly. It dispatches via job_create only.
"""

import json
from pathlib import Path

import pytest


# Path constants
ARI_AGENT_DIR = Path(__file__).parent.parent.parent / "agents" / "ari"
LEADER_AGENT_DIR = Path(__file__).parent.parent.parent / "agents" / "leader"

# Tool categories for testing (mirrors what the registry should contain).
# Ari is jober-hybrid — it has the 'job' category but NOT the 'instance'
# category. Direct execution capabilities are provided by 'bash' and
# 'filesystem'. Job delegation handles work that other agents own.
TOOL_CATEGORIES: dict[str, list[str]] = {
    "bash": ["bash"],
    "filesystem": ["list_directory", "read_file", "write_file", "glob_files", "grep_files", "edit_file"],
    "time": ["time"],
    "instance": [
        "spawn_instance", "send_message", "terminate_instance",
        "list_instances", "get_instance_info",
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


# =============================================================================
# 1. Agent Auto-Discovery
# =============================================================================


class TestAriAutoDiscovery:
    """Tests for Ari agent auto-discovery via AgentRegistry."""

    def test_ari_directory_exists(self) -> None:
        """The agents/ari/ directory should exist."""
        assert ARI_AGENT_DIR.exists(), f"agents/ari/ directory not found at {ARI_AGENT_DIR}"
        assert ARI_AGENT_DIR.is_dir(), "agents/ari/ should be a directory"

    def test_ari_not_in_skip_dirs(self) -> None:
        """'ari' should NOT be in SKIP_DIRS (template/internal directories)."""
        from daemon.registry import SKIP_DIRS

        assert "ari" not in SKIP_DIRS, (
            "ari should NOT be in SKIP_DIRS — it is a real agent, not a template"
        )

    def test_ari_discovered_in_registry(self) -> None:
        """Ari should be discovered when scanning the agents directory."""
        from daemon.registry import AgentRegistry

        agents_dir = ARI_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        assert registry.exists("ari"), "Ari should be discovered in the agents directory"

    def test_ari_in_agent_list(self) -> None:
        """Ari should appear in the list of all agents."""
        from daemon.registry import AgentRegistry

        agents_dir = ARI_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        agents = registry.list_all()
        agent_ids = {a.id for a in agents}
        assert "ari" in agent_ids, "ari should be in the list of agents"

    def test_ari_metadata_loaded_correctly(self) -> None:
        """Ari metadata loaded from registry should match meta.json."""
        from daemon.registry import AgentRegistry

        agents_dir = ARI_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        ari = registry.get("ari")
        assert ari is not None, "Ari should be retrievable from registry"
        assert ari.id == "ari"
        assert ari.name == "Ari"


# =============================================================================
# 2. meta.json Validity
# =============================================================================


class TestAriMetaJsonValidation:
    """Tests for Ari meta.json structure and content."""

    def test_meta_json_exists(self) -> None:
        """meta.json file should exist in the Ari agent directory."""
        meta_path = ARI_AGENT_DIR / "meta.json"
        assert meta_path.exists(), f"meta.json not found at {meta_path}"

    def test_meta_json_is_valid_json(self) -> None:
        """meta.json should be parseable as valid JSON."""
        meta_path = ARI_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        assert isinstance(meta, dict)

    def test_required_fields_exist(self) -> None:
        """meta.json should contain all required fields."""
        meta_path = ARI_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        required_fields = ["id", "name", "description", "icon", "color", "version", "tools"]
        for field in required_fields:
            assert field in meta, f"Required field '{field}' missing from meta.json"

    def test_agent_id_and_name(self) -> None:
        """Agent id and name should be 'ari' / 'Ari'."""
        meta_path = ARI_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        assert meta.get("id") == "ari", f"Agent id should be 'ari', got '{meta.get('id')}'"
        assert meta.get("name") == "Ari", f"Agent name should be 'Ari', got '{meta.get('name')}'"

    def test_field_types_are_correct(self) -> None:
        """meta.json field types should match expected types."""
        meta_path = ARI_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        assert isinstance(meta.get("id"), str), "id should be a string"
        assert isinstance(meta.get("name"), str), "name should be a string"
        assert isinstance(meta.get("description"), str), "description should be a string"
        assert isinstance(meta.get("icon"), str), "icon should be a string"
        assert isinstance(meta.get("color"), str), "color should be a string"
        assert isinstance(meta.get("innate_skills"), list), "innate_skills should be a list"
        assert isinstance(meta.get("tools"), dict), "tools should be a dict"

    def test_innate_skills_includes_job_orchestration(self) -> None:
        """Ari is a jober agent — innate_skills must include 'job-orchestration'.

        The 'job-orchestration' innate skill provides the job_create /
        job_watch / job_continue pattern that Ari uses to dispatch to
        Leader (Mode 2) and Worker (Mode 3).
        """
        meta_path = ARI_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        innate_skills = meta.get("innate_skills", [])
        assert "job-orchestration" in innate_skills, (
            f"Ari innate_skills must include 'job-orchestration' (jober pattern), got: {innate_skills}"
        )

    def test_innate_skills_includes_todo(self) -> None:
        """Ari's innate_skills must include 'todo'.

        Mode 1.5 quick tasks (>5 steps handled directly) require todo
        tracking so Ari can keep her head straight during multi-step
        direct execution.
        """
        meta_path = ARI_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        innate_skills = meta.get("innate_skills", [])
        assert "todo" in innate_skills, (
            f"Ari innate_skills should include 'todo', got: {innate_skills}"
        )

    def test_tools_config_structure(self) -> None:
        """tools configuration should have the expected structure."""
        meta_path = ARI_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        tools_config = meta.get("tools")
        assert tools_config is not None, "tools config should not be None"
        assert "allow" in tools_config, "tools config should have 'allow' key"
        assert isinstance(tools_config["allow"], list), "'allow' should be a list"
        assert len(tools_config["allow"]) > 0, "'allow' should not be empty"

    def test_system_is_false(self) -> None:
        """Ari is a user-facing front-door agent — system should be false.

        'system: true' is reserved for framework agents (e.g. _prompt_system,
        _mother). Ari is a real customer-facing assistant.
        """
        meta_path = ARI_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        assert meta.get("system") is False, (
            f"Ari should have system=false (front-door user-facing agent), got: {meta.get('system')}"
        )


# =============================================================================
# 3. Tool Filter Configuration
# =============================================================================


class TestAriToolFilter:
    """Tests for Ari tool filter parsing and jober-hybrid contents.

    Ari is a jober-hybrid agent:
      - Has 'job' tool category (jober capability for delegation)
      - Has 'bash' + 'filesystem' tool categories (hybrid direct execution)
      - Does NOT have 'instance' tool category (no spawn_instance)
      - Delegates specialized work to other agents via job_create
    """

    def test_ari_tool_filter_parsed_by_registry(self) -> None:
        """Ari's tools config should be parseable by the registry ToolFilter model."""
        from daemon.registry import AgentRegistry, ToolFilter

        agents_dir = ARI_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        ari = registry.get("ari")
        assert ari is not None
        assert ari.tools is not None
        assert isinstance(ari.tools, ToolFilter)
        assert ari.tools.deny is not None
        assert "edit_file" in ari.tools.deny
        assert "write_file" in ari.tools.deny
        assert ari.tools.allow is not None
        assert len(ari.tools.allow) > 0

    def test_ari_has_job_tool_in_allow(self) -> None:
        """Ari is a jober agent — tools.allow must contain 'job' (the category).

        The 'job' category grants job_create, job_watch, job_dispatch,
        job_terminate, job_attach — the core jober surface.
        """
        meta_path = ARI_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        allow = meta.get("tools", {}).get("allow", [])
        assert "job" in allow, (
            f"Ari tools.allow must include 'job' (jober capability), got: {allow}"
        )

    def test_ari_has_bash_and_filesystem_in_allow(self) -> None:
        """Ari is a jober-HYBRID — must have BOTH bash AND filesystem.

        Direct execution tools (bash + filesystem) enable Mode 1 quick
        tasks and Mode 1.5 multi-step direct execution. This is what
        distinguishes Ari from a pure jober (which would only have 'job'
        and NOT bash/filesystem).
        """
        meta_path = ARI_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        allow = meta.get("tools", {}).get("allow", [])
        assert "bash" in allow, (
            f"Ari tools.allow must include 'bash' (hybrid direct execution), got: {allow}"
        )
        assert "filesystem" in allow, (
            f"Ari tools.allow must include 'filesystem' (hybrid direct execution), got: {allow}"
        )

    def test_ari_does_not_have_instance_in_allow(self) -> None:
        """Ari must NOT have 'instance' in tools.allow — no spawn_instance.

        Ari dispatches via job_create, not instance tools. Having the
        'instance' category would grant spawn_instance / send_message /
        terminate_instance — which would let Ari bypass the job queue
        and create circular dispatch patterns. Ari is the front door
        and must use the job-queue-mediated path.
        """
        meta_path = ARI_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        allow = meta.get("tools", {}).get("allow", [])
        assert "instance" not in allow, (
            f"Ari tools.allow must NOT include 'instance' (no spawn_instance), got: {allow}"
        )

        # Double-check: resolve_tool_filter should also exclude the resolved
        # instance tools, not just the category name.
        from daemon.tools.instance import resolve_tool_filter

        allowed_tools = resolve_tool_filter(
            allow=allow,
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )
        assert allowed_tools is not None
        instance_tools = {
            "spawn_instance", "send_message", "terminate_instance",
            "list_instances", "get_instance_info",
        }
        for tool in instance_tools:
            assert tool not in allowed_tools, (
                f"{tool} should NOT be in Ari allowed tools — Ari dispatches via job_*"
            )

    # =============================================================================
# 4. Prompt Composition
# =============================================================================


class TestAriPromptComposition:
    """Tests for Ari agent prompt file presence, content, and loadability."""

    def test_soul_md_contains_personality_traits(self) -> None:
        """soul.md should describe Ari's personality: smart, friendly, warm.

        Ari's identity is the front-door virtual assistant. Her tone is
        a deliberate design choice — friendly, warm, smart. The
        personality vocabulary must appear in soul.md.
        """
        soul_path = ARI_AGENT_DIR / "soul.md"
        assert soul_path.exists(), f"soul.md not found at {soul_path}"
        content = soul_path.read_text(encoding="utf-8")

        content_lower = content.lower()
        assert "smart" in content_lower, "soul.md should mention 'smart' personality trait"
        assert "friendly" in content_lower, "soul.md should mention 'friendly' personality trait"
        assert "warm" in content_lower, "soul.md should mention 'warm' personality trait"

    def test_rule_md_contains_trueauto_and_triage_tree(self) -> None:
        """rule.md must contain TrueAuto mode + TASK TRIAGE decision tree.

        Two core invariants from the spec:
        1. TrueAuto is the default autonomy mode (decision-making posture)
        2. TASK TRIAGE is the routing decision tree (quick → direct,
           dev → Leader)
        """
        rule_path = ARI_AGENT_DIR / "rule.md"
        assert rule_path.exists(), f"rule.md not found at {rule_path}"
        content = rule_path.read_text(encoding="utf-8")

        assert "TrueAuto" in content, (
            "rule.md should contain 'TrueAuto' (default autonomy mode)"
        )
        # Case-insensitive check for triage — the heading uses 🚨 CRITICAL: TASK TRIAGE
        assert "triage" in content.lower(), (
            "rule.md should contain 'triage' decision tree"
        )

    def test_workflow_md_contains_two_modes_and_delegation(self) -> None:
        """workflow.md must describe Mode 1 + Mode 2 + delegation via job_create.

        The 2 modes are the core architecture:
          - Mode 1: quick tasks done directly
          - Mode 2: dev → Leader delegation via job_create(watch=True)
        Delegation uses ``job_create`` with the atomic watch pattern — the
        dispatch surface, not escalation. The triage decision tree and the
        Watch Job Discipline sections cover job lifecycle handling.
        """
        workflow_path = ARI_AGENT_DIR / "workflow.md"
        assert workflow_path.exists(), f"workflow.md not found at {workflow_path}"
        content = workflow_path.read_text(encoding="utf-8")

        # Both modes must be present
        for mode in ("Mode 1", "Mode 2"):
            assert mode in content, (
                f"workflow.md should define '{mode}' (2-mode architecture), got: missing"
            )

        # Delegation mechanism — job_create is the dispatch primitive.
        assert "job_create" in content, (
            "workflow.md should describe job_create as the delegation mechanism"
        )

    def test_user_md_exists(self) -> None:
        """user.md should exist (the user-facing capabilities doc)."""
        user_path = ARI_AGENT_DIR / "user.md"
        assert user_path.exists(), f"user.md not found at {user_path}"
        assert user_path.is_file(), f"user.md is not a regular file: {user_path}"
        content = user_path.read_text(encoding="utf-8")
        assert len(content) > 0, "user.md should not be empty"

    def test_job_orchestration_innate_skill_loads(self) -> None:
        """With innate_skills including 'job-orchestration', the skill loads.

        This verifies the prompt-composition pipeline (load_agent_skills)
        accepts Ari's meta.json and resolves the 'job-orchestration' skill
        to actual content. The skill is what gives Ari the job_create /
        job_watch / job_continue workflow knowledge.
        """
        from daemon.loader import load_agent_skills

        meta_path = ARI_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        skills = load_agent_skills(ARI_AGENT_DIR, meta)

        assert "job-orchestration" in skills, (
            f"job-orchestration skill should be loaded for Ari, got: {list(skills.keys())}"
        )
        assert isinstance(skills["job-orchestration"], str)
        assert len(skills["job-orchestration"]) > 0, (
            "job-orchestration skill content should be non-empty"
        )


# =============================================================================
# 5. No Team Members / No Spawn Authorization (front-door contract)
# =============================================================================


class TestAriNoTeamMembers:
    """Tests for Ari's no-spawn-authorization contract.

    Ari is the front-door agent. She dispatches via job_create (the job
    queue), NOT via spawn_instance (direct instance tools). This is the
    circular-dispatch-prevention rule: Ari routes to Leader and Worker
    through the job system so the job-queue's lifecycle and watch
    semantics apply. Direct spawn_instance would bypass those guarantees.
    """

    def test_ari_has_no_team_members_field(self) -> None:
        """meta.json should have NO team_members field, OR it should be empty.

        Mirrors the Worker contract (test_worker_agent.py
        TestWorkerNoTeamMembers). With no instance tools in tools.allow,
        team_members would be moot — but the spec requires both: the
        tool layer (no 'instance' category) AND the team_members layer
        (empty list) MUST both be in agreement.
        """
        meta_path = ARI_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        team_members = meta.get("team_members", [])

        # Field is either missing or an empty list — no spawn authorization
        assert team_members == [] or team_members is None, (
            f"Ari should have NO team_members (front door dispatches via job_*), got: {team_members}"
        )

    def test_ari_has_no_spawn_authorization(self) -> None:
        """Ari must not have spawn_instance — verified from tools.allow.

        Follows from the jober-hybrid design: Ari dispatches via
        job_create → Leader/Worker. spawn_instance would let Ari
        bypass the job queue and create an unmanaged dispatch path.
        This is the dual-enforcement: tool layer + team_members layer.
        """
        meta_path = ARI_AGENT_DIR / "meta.json"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        allow = meta.get("tools", {}).get("allow", [])

        # The 'instance' category must be absent — already verified in
        # TestAriToolFilter, but here we re-assert in the no-spawn-authorization
        # contract class so the contract is self-contained.
        assert "instance" not in allow, (
            f"Ari cannot spawn_instance — 'instance' must NOT be in tools.allow, got: {allow}"
        )

        # Equivalently, after resolving the allow list through the tool
        # category mapping, 'spawn_instance' itself must not be granted.
        from daemon.tools.instance import resolve_tool_filter

        allowed_tools = resolve_tool_filter(
            allow=allow,
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )
        assert allowed_tools is not None
        assert "spawn_instance" not in allowed_tools, (
            f"Ari must not have spawn_instance (jober dispatches via job_create), "
            f"got: {allowed_tools}"
        )
