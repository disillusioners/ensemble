"""Comprehensive tests for the Gaia agent (v2.0.0 — environment terraformer).

Gaia was redefined from a guide-only "environment setup assistant" (npx scenario
scripts) into the ENVIRONMENT-TERRAFORM agent: it provisions, configures,
upgrades, healthchecks, and repairs ensemble's machine infrastructure, with the
mcps_client lifecycle (build / sign / install / config / upgrade / healthcheck /
repair) as its first scope.

These tests pin the NEW shape end-to-end: meta.json identity + exact tool
allow-list, real-registry discovery, prompt composition (soul → rule → tools →
workflow), the lifecycle runbook content, and the negative space (no privileged
categories, no spawn authority, no deny-list model). All tests run in the unit
test environment with langgraph mocks from conftest.py.
"""

import json
from pathlib import Path

import pytest


# Path constants
GAIA_AGENT_DIR = Path(__file__).parent.parent.parent / "agents" / "gaia"

# The EXACT allow-list Gaia v2.0.0 must carry: bash/proc/filesystem = the core
# terraforming mandate; help = convention; system = READ-ONLY daemon info for
# healthchecks. Everything else (project, instance, mcp, shared_meta_kv, service,
# system_upgrade, system-log, ens-db, mcp_rpc) is absent BY OMISSION — Gaia's
# grant model is a strict allow-list with no deny field.
GAIA_TOOLS_ALLOW = ["bash", "proc", "filesystem", "help", "system"]

# Tool categories for synthetic filter-resolution tests (mirrors the shape of the
# real registry's categories; keeps the negative fixtures self-contained).
TOOL_CATEGORIES: dict[str, list[str]] = {
    "bash": ["bash"],
    "proc": ["proc_list", "proc_logs", "proc_run", "proc_status", "proc_stop"],
    "filesystem": ["list_directory", "read_file", "write_file", "glob_files", "grep_files", "edit_file"],
    "system": ["system_env", "system_config", "system_health"],
    "time": ["time"],
    "instance": [
        "spawn_instance", "send_message", "terminate_instance",
        "list_instances", "get_instance_info"
    ],
    "self": ["inner_soul", "access_memory"],
    "project": [
        "project_create", "project_get", "project_list", "project_search",
        "project_get_by_instance", "project_get_by_directory", "project_update",
    ],
    "help": ["tool_help"],
}


def _load_meta() -> dict:
    meta_path = GAIA_AGENT_DIR / "meta.json"
    with open(meta_path, "r", encoding="utf-8") as f:
        return json.load(f)


# =============================================================================
# 1. Meta.json Validation — the v2.0.0 shape
# =============================================================================


class TestGaiaMetaJsonValidation:
    """Tests for Gaia's meta.json structure and content (environment-terraform shape)."""

    def test_meta_json_exists(self) -> None:
        """meta.json file should exist in the Gaia agent directory."""
        assert (GAIA_AGENT_DIR / "meta.json").exists(), (
            f"meta.json not found at {GAIA_AGENT_DIR / 'meta.json'}"
        )

    def test_meta_json_is_valid_json(self) -> None:
        """meta.json should be parseable as valid JSON."""
        assert isinstance(_load_meta(), dict)

    def test_required_fields_exist(self) -> None:
        """meta.json should contain all required fields."""
        meta = _load_meta()
        for field in ["id", "name", "description", "icon", "color", "version", "system",
                      "capabilities", "tags", "innate_skills", "tools", "llm_model",
                      "team_members"]:
            assert field in meta, f"Required field '{field}' missing from meta.json"

    def test_agent_name_is_gaia(self) -> None:
        """Agent id and name should be 'gaia' / 'Gaia'."""
        meta = _load_meta()
        assert meta.get("id") == "gaia", f"Agent id should be 'gaia', got '{meta.get('id')}'"
        assert meta.get("name") == "Gaia", f"Agent name should be 'Gaia', got '{meta.get('name')}'"

    def test_description_is_terraform_purpose(self) -> None:
        """The description must state the environment-terraform purpose (purpose redefinition)."""
        description = _load_meta().get("description", "")
        assert description.startswith("Environment terraformer"), (
            "description should open with the environment-terraform purpose, "
            f"got: {description[:80]!r}"
        )
        assert "mcps_client" in description, (
            "description should name the first scope: mcps_client lifecycle"
        )

    def test_version_is_2_0_0(self) -> None:
        """The purpose redefinition is a major version bump: version must be 2.0.0."""
        assert _load_meta().get("version") == "2.0.0"

    def test_identity_fields_unchanged(self) -> None:
        """Icon, color, and the non-system flag survive the redefinition unchanged."""
        meta = _load_meta()
        assert meta.get("icon") == "🌍"
        assert meta.get("color") == "accent-green"
        assert meta.get("system") is False
        assert meta.get("llm_model") is None

    def test_tags_are_terraform_tags(self) -> None:
        """tags must carry the new domain vocabulary."""
        meta = _load_meta()
        assert meta.get("tags") == ["environment", "terraform", "mcps-client", "infrastructure"]

    def test_capabilities_are_terraform_capabilities(self) -> None:
        """capabilities must carry the two new capability slugs."""
        meta = _load_meta()
        assert meta.get("capabilities") == ["environment-terraform", "mcps-client-lifecycle"]

    def test_innate_skills_unchanged(self) -> None:
        """innate_skills stays ['todo'] — no new innate skills in the redefinition."""
        assert _load_meta().get("innate_skills") == ["todo"]

    def test_team_members_empty_no_spawn_authority(self) -> None:
        """team_members must stay [] — Gaia spawns nothing; it reports to its dispatcher."""
        meta = _load_meta()
        assert meta.get("team_members") == [], (
            "gaia must hold NO team_members (empty list = no spawn authority)"
        )

    def test_tools_allow_exact_list(self) -> None:
        """tools.allow must be EXACTLY the five-category terraforming set."""
        meta = _load_meta()
        assert meta["tools"]["allow"] == GAIA_TOOLS_ALLOW

    def test_tools_no_deny_field_allow_list_model(self) -> None:
        """Gaia's grant model is allow-list-only: no deny key, and deny resolves to None."""
        meta = _load_meta()
        assert "deny" not in meta["tools"], (
            "tools must NOT carry a deny field — prohibitions are enforced by the "
            "allow-list (absent = not granted), never by a deny list"
        )
        assert meta["tools"].get("deny") is None

    def test_field_types_are_correct(self) -> None:
        """meta.json field types should match expected types."""
        meta = _load_meta()
        assert isinstance(meta.get("id"), str)
        assert isinstance(meta.get("name"), str)
        assert isinstance(meta.get("description"), str)
        assert isinstance(meta.get("icon"), str)
        assert isinstance(meta.get("color"), str)
        assert isinstance(meta.get("version"), str)
        assert isinstance(meta.get("system"), bool)
        assert isinstance(meta.get("capabilities"), list)
        assert isinstance(meta.get("tags"), list)
        assert isinstance(meta.get("innate_skills"), list)
        assert isinstance(meta.get("team_members"), list)
        assert "tools" in meta

    def test_scenario_dir_is_gone(self) -> None:
        """The obsolete guide-only scenario/ directory (npx.md) must not exist."""
        assert not (GAIA_AGENT_DIR / "scenario").exists(), (
            "the obsolete scenario/ directory must be deleted in the redefinition"
        )


# =============================================================================
# 2. Agent Registry Discovery
# =============================================================================


class TestGaiaRegistryDiscovery:
    """Tests for Gaia agent discovery via AgentRegistry."""

    def _registry(self):
        from daemon.registry import AgentRegistry

        registry = AgentRegistry(GAIA_AGENT_DIR.parent)
        registry.discover()
        return registry

    def test_gaia_discovered_in_real_registry(self) -> None:
        """Gaia should be discovered when scanning the real agents directory."""
        registry = self._registry()
        assert registry.exists("gaia"), "Gaia should be discovered in the agents directory"

    def test_gaia_in_agent_list(self) -> None:
        """Gaia should appear in the list of all agents."""
        registry = self._registry()
        agent_ids = {a.id for a in registry.list_all()}
        assert "gaia" in agent_ids, "gaia should be in the list of agents"

    def test_gaia_metadata_loaded_correctly(self) -> None:
        """Gaia metadata loaded from registry should match meta.json (v2.0.0 shape)."""
        gaia = self._registry().get("gaia")
        assert gaia is not None, "Gaia should be retrievable from registry"
        assert gaia.id == "gaia"
        assert gaia.name == "Gaia"
        assert gaia.description.startswith("Environment terraformer")
        assert "mcps_client" in gaia.description
        assert gaia.icon == "🌍"
        assert gaia.color == "accent-green"
        assert gaia.version == "2.0.0"
        assert gaia.system is False
        assert gaia.path == GAIA_AGENT_DIR

    def test_gaia_tools_config_in_registry(self) -> None:
        """Gaia's tool filter should be correctly loaded into registry."""
        from daemon.registry import ToolFilter

        gaia = self._registry().get("gaia")
        assert gaia is not None
        assert gaia.tools is not None
        assert isinstance(gaia.tools, ToolFilter)
        assert gaia.tools.allow == GAIA_TOOLS_ALLOW
        assert gaia.tools.deny is None

    def test_gaia_tags_and_capabilities(self) -> None:
        """Gaia should have the terraform tags and capabilities."""
        gaia = self._registry().get("gaia")
        assert gaia is not None
        assert gaia.tags == ["environment", "terraform", "mcps-client", "infrastructure"]
        assert gaia.capabilities == ["environment-terraform", "mcps-client-lifecycle"]


# =============================================================================
# 3. Agent Loading (System Prompt Composition)
# =============================================================================


class TestGaiaAgentLoading:
    """Tests for Gaia agent loading and system prompt composition."""

    def test_load_gaia_prompts(self) -> None:
        """All Gaia markdown files should be loadable as prompts."""
        from daemon.loader import load_agent_prompts

        prompts = load_agent_prompts(GAIA_AGENT_DIR)

        assert "soul" in prompts, "soul.md should be loaded"
        assert "rule" in prompts, "rule.md should be loaded"
        assert "workflow" in prompts, "workflow.md should be loaded"
        assert "tools" in prompts, "tools_note.md should be loaded"

    def test_soul_content_is_terraform_identity(self) -> None:
        """soul.md should carry the terraformer identity and executor nature."""
        from daemon.loader import load_agent_prompts

        soul = load_agent_prompts(GAIA_AGENT_DIR)["soul"]

        assert "Gaia" in soul
        assert "environment terraformer" in soul.lower()
        assert "executor" in soul.lower(), "soul must state the EXECUTOR nature"
        assert "healthcheck" in soul.lower()

    def test_rule_content_cardinals_plus_guidelines(self) -> None:
        """rule.md must carry the Cardinal Rules / Guidelines split."""
        from daemon.loader import load_agent_prompts

        rule = load_agent_prompts(GAIA_AGENT_DIR)["rule"]

        assert "Cardinal Rules" in rule
        assert "Guidelines" in rule
        # the four mission-mandated cardinals
        assert "file-to-file" in rule.lower(), "token hygiene cardinal missing"
        assert "mcps-client-signing" in rule, "stable signing identity cardinal missing"
        assert "ad-hoc" in rule.lower(), "never-ad-hoc-sign cardinal missing"
        assert "0600" in rule, "config mode 0600 cardinal missing"
        # the OS-blocked class
        assert "OS-BLOCKED" in rule.upper() or "os-blocked" in rule.lower()

    def test_cardinal_rules_capped_at_seven(self) -> None:
        """The Cardinal Rules section holds at most 7 numbered rules (guide rule)."""
        from daemon.loader import load_agent_prompts

        rule = load_agent_prompts(GAIA_AGENT_DIR)["rule"]
        section = rule.split("## Cardinal Rules", 1)[1].split("## Guidelines", 1)[0]
        numbered = [line for line in section.splitlines() if line.strip().startswith(("1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9."))]
        assert 1 <= len(numbered) <= 7, (
            f"Cardinal Rules must be ≤7 numbered rules, found {len(numbered)}"
        )

    def test_tools_note_content_included(self) -> None:
        """tools_note.md content should be included as the tools prompt."""
        from daemon.loader import load_agent_prompts

        tools = load_agent_prompts(GAIA_AGENT_DIR)["tools"]

        assert "Tool Usage Notes" in tools
        for category in ("bash", "filesystem", "proc", "system", "help"):
            assert category in tools, f"tools_note should cover the {category} grant"

    def test_compose_system_prompt_includes_all_sections(self) -> None:
        """Composed system prompt should include soul, rule, tools, workflow."""
        from daemon.loader import compose_system_prompt, load_agent_prompts

        prompts = load_agent_prompts(GAIA_AGENT_DIR)
        system_prompt = compose_system_prompt(prompts)

        assert "Gaia" in system_prompt, "soul content should be in system prompt"
        assert "Cardinal Rules" in system_prompt, "rule content should be in system prompt"
        assert "Lifecycle Runbook" in system_prompt, "workflow content should be in system prompt"
        assert "Tool Usage Notes" in system_prompt, "tools content should be in system prompt"

    def test_system_prompt_composition_order(self) -> None:
        """System prompt should follow correct order: soul → rule → tools → workflow."""
        from daemon.loader import compose_system_prompt, load_agent_prompts

        prompts = load_agent_prompts(GAIA_AGENT_DIR)
        system_prompt = compose_system_prompt(prompts)

        soul_pos = system_prompt.find("Gaia")
        rule_pos = system_prompt.find("# Rules")
        tools_pos = system_prompt.find("# Tool Usage Notes")
        workflow_pos = system_prompt.find("# Workflow")

        assert soul_pos != -1, "soul (Gaia) should be in system prompt"
        assert rule_pos != -1, "rule should be in system prompt"
        assert tools_pos != -1, "tools should be in system prompt"
        assert workflow_pos != -1, "workflow should be in system prompt"

        assert soul_pos < rule_pos, "soul should come before rule"
        assert rule_pos < tools_pos, "rule should come before tools"
        assert tools_pos < workflow_pos, "tools should come before workflow"

    def test_load_and_cache_prompt_works(self) -> None:
        """load_and_cache_prompt should successfully load Gaia's prompt."""
        from daemon.loader import PromptCache, load_and_cache_prompt

        cache = PromptCache()
        prompt, tokens = load_and_cache_prompt("gaia", GAIA_AGENT_DIR, cache)

        assert isinstance(prompt, str)
        assert len(prompt) > 0
        assert tokens > 0
        assert "Gaia" in prompt
        assert "Cardinal Rules" in prompt

    def test_load_and_cache_prompt_caches(self) -> None:
        """load_and_cache_prompt should cache the result."""
        from daemon.loader import PromptCache, load_and_cache_prompt

        cache = PromptCache()

        prompt1, tokens1 = load_and_cache_prompt("gaia", GAIA_AGENT_DIR, cache)

        cached = cache.get("gaia")
        assert cached is not None
        assert cached[0] == prompt1
        assert cached[1] == tokens1

    def test_no_errors_during_loading(self) -> None:
        """Loading Gaia should not raise any exceptions."""
        from daemon.loader import (
            PromptCache,
            compose_system_prompt,
            load_agent_prompts,
            load_and_cache_prompt,
        )

        prompts = load_agent_prompts(GAIA_AGENT_DIR)
        system_prompt = compose_system_prompt(prompts)
        cache = PromptCache()
        prompt, _tokens = load_and_cache_prompt("gaia", GAIA_AGENT_DIR, cache)

        assert len(system_prompt) > 0
        assert len(prompt) > 0


# =============================================================================
# 4. The mcps_client Lifecycle Runbook (the core artifact)
# =============================================================================


class TestGaiaRunbookContent:
    """The workflow.md runbook must encode the full mcps_client lifecycle."""

    def _workflow(self) -> str:
        from daemon.loader import load_agent_prompts

        return load_agent_prompts(GAIA_AGENT_DIR)["workflow"]

    def test_all_lifecycle_phase_markers_present(self) -> None:
        """BUILD / SIGN / INSTALL / CONFIG / HEALTHCHECK / TCC / UPGRADE / UNINSTALL."""
        workflow = self._workflow()
        for marker in ("BUILD", "SIGN", "INSTALL", "CONFIG", "HEALTHCHECK",
                       "TCC CEREMONY", "UPGRADE", "UNINSTALL"):
            assert marker in workflow, f"runbook phase marker missing: {marker}"

    def test_stable_signing_identity_pinned(self) -> None:
        """The runbook pins the stable signing identity and forbids ad-hoc signing."""
        workflow = self._workflow()
        assert "MCPS_SIGN_IDENTITY=mcps-client-signing" in workflow
        assert "ad-hoc" in workflow.lower()
        assert "8C9F52D3932FCFF7ACC0BD84A8358AB050183DF4" in workflow, (
            "the public signing fingerprint (safe metadata) should be pinned for verification"
        )

    def test_build_environment_pinned(self) -> None:
        """The runbook pins the toolchain: go path, nvm node 22, build entry points."""
        workflow = self._workflow()
        assert "/usr/local/go/bin" in workflow
        assert "nvm" in workflow
        assert "make build" in workflow
        assert "scripts/build.sh" in workflow

    def test_install_steps_pinned(self) -> None:
        """The runbook pins ditto install, the admin fallback, and the LaunchAgent installer."""
        workflow = self._workflow()
        assert "ditto dist/McpsClient.app /Applications/McpsClient.app" in workflow
        assert "administrator privileges" in workflow, "admin fallback (user types password) missing"
        assert "scripts/install-launchagent.sh install" in workflow
        # the PATH trap: installer bakes the node runtime — re-run after node upgrades
        assert "nvm" in workflow and ("PATH trap" in workflow or "bakes" in workflow)

    def test_config_and_token_hygiene_pinned(self) -> None:
        """The runbook pins the config path, mode, center URL, and file-to-file token flow."""
        workflow = self._workflow()
        assert "~/.config/mcps-client/config.yaml" in workflow
        assert "0600" in workflow
        assert "ws://127.0.0.1:9797/api/mcps-gateway/ws" in workflow
        assert "MCPS_GATEWAY_TOKENS" in workflow
        assert "file-to-file" in workflow.lower(), "token file-to-file flow missing"

    def test_healthcheck_ladder_pinned(self) -> None:
        """The runbook pins the healthcheck evidence ladder."""
        workflow = self._workflow()
        assert "/livez" in workflow
        assert "401" in workflow, "the WS probe expecting 401 + WWW-Authenticate missing"
        assert "WWW-Authenticate" in workflow
        assert "launchctl" in workflow
        assert "~/Library/Logs/McpsClient" in workflow
        assert "loopback" in workflow.lower(), "the expected-noise loopback warning must be documented"

    def test_tcc_ceremony_handoff_pinned(self) -> None:
        """The runbook encodes the OS-blocked TCC hand-off: detect → instruct → verify → continue."""
        workflow = self._workflow()
        assert "Accessibility" in workflow
        assert "Screen" in workflow, "the Screen & System Audio Recording pane missing"
        assert "x-apple.systempreferences" in workflow
        assert "kickstart" in workflow

    def test_verify_after_handoff_loop_present(self) -> None:
        """The runbook names the verify-after-handoff loop explicitly."""
        workflow = self._workflow()
        assert "Verify-After-Handoff" in workflow
        lowered = workflow.lower()
        for step in ("detect", "hand off", "wait", "verify", "continue"):
            assert step in lowered, f"loop step missing: {step}"

    def test_uninstall_leaves_tcc_cleanup_note(self) -> None:
        """The runbook requires a TCC cleanup note on every uninstall."""
        workflow = self._workflow()
        assert "install-launchagent.sh uninstall" in workflow
        assert "cleanup note" in workflow.lower()

    def test_upgrade_preserves_config_and_verifies(self) -> None:
        """The upgrade path must prove config preservation (before/after checksum)."""
        workflow = self._workflow()
        assert "git pull" in workflow
        assert "BEFORE" in workflow and "AFTER" in workflow

    def test_reference_library_uses_fenced_paths_only(self) -> None:
        """Repo doc paths appear only inside fenced blocks; prose names the docs."""
        workflow = self._workflow()
        assert "mcps-gateway.md" in workflow, "the gateway reference should be in the fenced doc map"
        assert "13" in workflow and "MCPS_GATEWAY_" in workflow, (
            "the 13-knob fact should be stated"
        )
        assert "fail-closed" in workflow.lower(), "MCPS_GATEWAY_TOKENS fail-closed semantics missing"
        assert "restart" in workflow.lower(), "knob flips need daemon restart — must be stated"


# =============================================================================
# 5. Negative Pins — the allow-list boundary
# =============================================================================


class TestGaiaNegativePins:
    """Gaia must NOT carry privileged, spawn-capable, or coordination categories."""

    def test_no_privileged_categories_in_allow(self) -> None:
        """No system_upgrade / system-log / ens-db / mcp_rpc in the allow-list."""
        meta = _load_meta()
        allowed = set(meta["tools"]["allow"])
        for forbidden in ("system_upgrade", "system-log", "ens-db", "mcp_rpc",
                          "db", "ens-db-tools"):
            assert forbidden not in allowed, (
                f"privileged category '{forbidden}' must NOT be granted to gaia"
            )

    def test_no_spawn_or_coordination_categories(self) -> None:
        """No instance / shared_meta_kv / service / project / mcp / self / time categories."""
        meta = _load_meta()
        allowed = set(meta["tools"]["allow"])
        for forbidden in ("instance", "shared_meta_kv", "service", "project",
                          "mcp", "self", "time", "knowledge"):
            assert forbidden not in allowed, (
                f"category '{forbidden}' must NOT be granted to gaia"
            )

    def test_allow_list_is_exactly_five_categories(self) -> None:
        """The allow-list carries exactly the five sanctioned categories — nothing else."""
        assert sorted(_load_meta()["tools"]["allow"]) == sorted(GAIA_TOOLS_ALLOW)

    def test_synthetic_filter_resolution_matches_grant(self) -> None:
        """Resolving Gaia's allow-list over synthetic categories yields no spawn/privileged tools."""
        from daemon.tools.instance import resolve_tool_filter

        allowed_tools = resolve_tool_filter(
            allow=list(GAIA_TOOLS_ALLOW),
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )

        assert allowed_tools is not None
        # the sanctioned surfaces resolve
        assert "bash" in allowed_tools
        assert "tool_help" in allowed_tools
        for tool in ("list_directory", "read_file", "write_file", "edit_file"):
            assert tool in allowed_tools, f"{tool} should be in Gaia's allowed tools"
        for tool in ("proc_list", "proc_run", "proc_status"):
            assert tool in allowed_tools, f"{tool} should be in Gaia's allowed tools"
        for tool in ("system_env", "system_config", "system_health"):
            assert tool in allowed_tools, f"{tool} should be in Gaia's allowed tools"
        # the negative space stays negative
        spawn_tools = {"spawn_instance", "send_message", "terminate_instance",
                       "list_instances", "get_instance_info"}
        for tool in spawn_tools:
            assert tool not in allowed_tools, f"{tool} should NOT be in Gaia's allowed tools"
        for tool in ("project_create", "project_get", "project_list"):
            assert tool not in allowed_tools, f"{tool} should NOT be in Gaia's allowed tools"
        for tool in ("inner_soul", "access_memory", "time"):
            assert tool not in allowed_tools, f"{tool} should NOT be in Gaia's allowed tools"

    def test_spawn_capable_allow_is_negative_control(self) -> None:
        """Negative fixture: an allow-list WITH instance does grant spawn tools (guard liveness)."""
        from daemon.tools.instance import resolve_tool_filter

        allowed_tools = resolve_tool_filter(
            allow=["instance"],
            deny=None,
            tool_categories=TOOL_CATEGORIES,
        )

        assert allowed_tools is not None
        assert "spawn_instance" in allowed_tools, (
            "the negative control must stay live: an instance grant DOES resolve spawn tools"
        )


# =============================================================================
# 6. Tool Filtering (registry-level)
# =============================================================================


class TestGaiaToolFiltering:
    """Tests for Gaia's tool filtering configuration."""

    def test_gaia_tool_filter_from_registry(self) -> None:
        """Gaia should have the exact tool filter configured in the registry."""
        from daemon.registry import AgentRegistry

        registry = AgentRegistry(GAIA_AGENT_DIR.parent)
        registry.discover()

        gaia = registry.get("gaia")
        assert gaia is not None
        assert gaia.tools is not None
        assert gaia.tools.allow == GAIA_TOOLS_ALLOW

    def test_gaia_tools_doc_loads(self, monkeypatch) -> None:
        """load_tools_doc_for_agent should return docs for Gaia's allowed tools."""
        from daemon.loader import load_tools_doc_for_agent
        from daemon.registry import AgentRegistry
        from daemon.tools._tool_registry import _full_docs, _tool_metadata, clear_registry
        import daemon.registry

        # Initialize and discover an AgentRegistry using the correct agents directory,
        # following the existing project test pattern. load_tools_doc_for_agent()
        # resolves agent metadata through the global registry returned by
        # get_registry(); a freshly discovered registry wired into that global slot
        # guarantees gaia's tool filter is visible.
        registry = AgentRegistry(GAIA_AGENT_DIR.parent)
        registry.discover()

        # monkeypatch handles auto-restore of the module-level registry slot
        # on teardown, so neighbouring tests do not see this leaked registry.
        monkeypatch.setattr(daemon.registry, "_registry", registry)

        # Snapshot the current tool-registration state so we can restore it
        # after the test. clear_registry() mutates these dicts in-place, so
        # monkeypatch (which only handles attribute reassignment) cannot undo
        # the mutation — we restore explicitly in finally below.
        saved_metadata = dict(_tool_metadata)
        saved_full_docs = dict(_full_docs)

        try:
            # Reset _tool_metadata so load_tools_doc_for_agent's
            # _ensure_tool_metadata_populated() runs the full scan.
            clear_registry()
            docs = load_tools_doc_for_agent("gaia")

            assert isinstance(docs, str)
            assert len(docs) > 0
            # Should contain tool categories
            assert "Bash" in docs or "bash" in docs.lower()
            assert "File Operations" in docs or "Filesystem" in docs or "filesystem" in docs.lower()
            assert "Help" in docs or "help" in docs.lower()
        finally:
            # Restore the tool-registration state to avoid leaking it into
            # other tests that rely on the pre-populated tool metadata.
            _tool_metadata.clear()
            _tool_metadata.update(saved_metadata)
            _full_docs.clear()
            _full_docs.update(saved_full_docs)

    def test_gaia_tool_filter_config_parsed_correctly(self) -> None:
        """Gaia's tool filter should be parseable from meta.json."""
        from daemon.registry import ToolFilter

        tool_filter = ToolFilter.model_validate(_load_meta()["tools"])

        assert tool_filter.allow == GAIA_TOOLS_ALLOW
        assert tool_filter.deny is None


# =============================================================================
# Integration: Full Loading Pipeline
# =============================================================================


class TestGaiaFullLoadingPipeline:
    """Integration tests for the complete Gaia loading pipeline."""

    def test_gaia_loads_without_errors(self) -> None:
        """Gaia should load successfully through the full pipeline."""
        from daemon.loader import PromptCache, load_and_cache_prompt
        from daemon.registry import AgentRegistry

        registry = AgentRegistry(GAIA_AGENT_DIR.parent)
        registry.discover()

        gaia = registry.get("gaia")
        assert gaia is not None

        cache = PromptCache()
        prompt, tokens = load_and_cache_prompt("gaia", GAIA_AGENT_DIR, cache)

        assert len(prompt) > 1000, "Gaia's system prompt should be substantial"
        assert tokens > 100, "Gaia's token count should be significant"

    def test_gaia_system_prompt_is_self_contained(self) -> None:
        """Gaia's system prompt should contain all necessary sections."""
        from daemon.loader import PromptCache, load_and_cache_prompt

        cache = PromptCache()
        prompt, _ = load_and_cache_prompt("gaia", GAIA_AGENT_DIR, cache)

        assert "Gaia" in prompt, "Should contain identity (Gaia)"
        assert "Cardinal Rules" in prompt, "Should contain rules"
        assert "Lifecycle Runbook" in prompt, "Should contain the lifecycle runbook"
        assert ("Tools" in prompt or "tools" in prompt), "Should contain tools reference"

    def test_gaia_description_matches_meta(self) -> None:
        """Gaia's registry description should match meta.json."""
        from daemon.registry import AgentRegistry

        registry = AgentRegistry(GAIA_AGENT_DIR.parent)
        registry.discover()

        gaia = registry.get("gaia")
        assert gaia.description == _load_meta()["description"]
