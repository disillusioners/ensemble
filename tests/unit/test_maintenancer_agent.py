"""Comprehensive validation pack for the Maintenancer agent (W1-P1).

Validates the maintenancer agent scaffolding + registration:

  1.  meta.json schema & required-field correctness (W1-P1 task 1.2)
  2.  Tool allow-list / deny-list contract per D20 (resolution-level strip)
      — system_restart MUST be in tools.deny; the call-time refusal at
        call time is env-conditional and does NOT fire on dev/demo.
  3.  Auto-discovery via AgentRegistry (W1-P1 task 1.11 EXTENDED per
      architect §4.3 — BASE + VERSIONED metas enumeration guard).
  4.  Convention compliance: prompt-file completeness, cardinal-rule
      count (≤7), first-person voice, no forbidden system tokens, END
      TURN + escape valve + report-sanity markers in workflow.md.
  5.  leader team_members includes "maintenancer" (W1-P1 task 1.9).

All tests are pure file + registry parsing — no daemon/DB startup, no
LLM calls. Modelled after ``tests/unit/test_project_manager_agent.py``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Path constants — resolve against the REAL agents/ dir at repo root.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MM_AGENT_DIR = PROJECT_ROOT / "agents" / "maintenancer"
META_PATH = MM_AGENT_DIR / "meta.json"
SOUL_PATH = MM_AGENT_DIR / "soul.md"
RULE_PATH = MM_AGENT_DIR / "rule.md"
WORKFLOW_PATH = MM_AGENT_DIR / "workflow.md"
TOOLS_NOTE_PATH = MM_AGENT_DIR / "tools_note.md"
MEMORY_PATH = MM_AGENT_DIR / "memory.md"
GROWTH_PATH = MM_AGENT_DIR / "growth.md"
SKILL_SET_PATH = MM_AGENT_DIR / "skill-set.yaml"
SKILLS_TEMPLATE_DIR = MM_AGENT_DIR / "skills-template"
KNOWLEDGE_DIR = MM_AGENT_DIR / "knowledge"

LEADER_META_PATH = PROJECT_ROOT / "agents" / "leader" / "meta.json"

PROMPT_FILES: tuple[Path, ...] = (
    SOUL_PATH,
    RULE_PATH,
    WORKFLOW_PATH,
    TOOLS_NOTE_PATH,
    MEMORY_PATH,
    GROWTH_PATH,
)


# ---------------------------------------------------------------------------
# Forbidden tokens — per W1-P1 task 1.11 assertion #8 (writing guide §1).
# ---------------------------------------------------------------------------
FORBIDDEN_TOKENS: tuple[str, ...] = (
    "meta.json",
    "daemon/",
    "_tool_registry",
    "tools.allow",
    "skill-set.yaml",
    "seed_all",
    "innate_skills",
    "default_agent_versions",
)


# Required tools — per meta.json contract (D20 enforcement).
REQUIRED_ALLOW: frozenset[str] = frozenset({
    "system-log",
    "ens-db",
    "knowledge",
    "system_upgrade",
    "db",
})

REQUIRED_DENY: frozenset[str] = frozenset({
    "git_commit",
    "edit_file",
    "write_file",
    "system_restart",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_meta() -> dict:
    """Load and return maintenancer/meta.json as a dict."""
    with open(META_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_leader_meta() -> dict:
    """Load and return leader/meta.json as a dict."""
    with open(LEADER_META_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _read(path: Path) -> str:
    """Read a UTF-8 text file."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_cardinal_section(rule_text: str) -> str:
    """Extract the text under the '## Cardinal Rules' heading.

    Returns the slice from that heading until the next '##' heading
    (exclusive) or end of file. Empty string if not found.
    """
    match = re.search(
        r"^##\s+Cardinal Rules[^\n]*\n(?P<body>.*?)(?=^##\s|\Z)",
        rule_text,
        flags=re.MULTILINE | re.DOTALL,
    )
    return match.group("body").strip() if match else ""


# =============================================================================
# 1. meta.json schema & configuration
# =============================================================================


class TestMetaJsonSchema:
    """meta.json is valid JSON and exposes the schema the project requires."""

    def test_meta_json_exists(self) -> None:
        assert META_PATH.exists(), f"meta.json not found at {META_PATH}"

    def test_meta_json_is_valid_json(self) -> None:
        meta = _load_meta()
        assert isinstance(meta, dict), (
            f"meta.json root should be a JSON object, got {type(meta).__name__}"
        )

    def test_required_fields_exist(self) -> None:
        meta = _load_meta()
        required = [
            "id",
            "name",
            "version",
            "innate_skills",
            "skill_injection",
            "tools",
            "team_members",
        ]
        missing = [f for f in required if f not in meta]
        assert not missing, f"Required fields missing from meta.json: {missing}"

    def test_agent_id(self) -> None:
        meta = _load_meta()
        assert meta["id"] == "maintenancer", (
            f"Expected id 'maintenancer', got '{meta.get('id')}'"
        )

    def test_agent_name(self) -> None:
        meta = _load_meta()
        assert meta["name"] == "Maintenancer", (
            f"Expected name 'Maintenancer', got '{meta.get('name')!r}'"
        )

    def test_version_is_semver_string(self) -> None:
        meta = _load_meta()
        version = meta.get("version")
        assert isinstance(version, str) and version, (
            f"version must be a non-empty string, got {version!r}"
        )
        assert re.match(r"^\d+\.\d+\.\d+", version), (
            f"version should look like semver (X.Y.Z...), got {version!r}"
        )

    def test_tools_has_allow_and_deny_lists(self) -> None:
        meta = _load_meta()
        tools = meta.get("tools", {})
        assert isinstance(tools.get("allow"), list), (
            f"tools.allow must be a list, got {type(tools.get('allow')).__name__}"
        )
        assert isinstance(tools.get("deny"), list), (
            f"tools.deny must be a list, got {type(tools.get('deny')).__name__}"
        )

    def test_innate_skills(self) -> None:
        meta = _load_meta()
        assert meta["innate_skills"] == ["dynamic-skill", "todo", "chart"], (
            f"innate_skills must be ['dynamic-skill', 'todo', 'chart'] "
            f"(worker/leader convention). Got: {meta['innate_skills']}"
        )

    def test_skill_injection_is_true(self) -> None:
        meta = _load_meta()
        assert meta["skill_injection"] is True, (
            f"skill_injection must be True (v2 dispatcher pattern). "
            f"Got: {meta['skill_injection']!r}"
        )

    def test_no_force_explore_is_true(self) -> None:
        meta = _load_meta()
        assert meta.get("no_force_explore") is True, (
            f"no_force_explore should be True (KB-targeted work; "
            f"force-explore wastes tokens). Got: {meta.get('no_force_explore')!r}"
        )


# =============================================================================
# 2. Tool allow-list / deny-list contract (W1-P1 task 1.11 + D20)
# =============================================================================


class TestToolAllowDenyContract:
    """W1-P1 task 1.2 contract — D20 resolution-level enforcement."""

    def test_allow_contains_required_categories(self) -> None:
        meta = _load_meta()
        allow = set(meta["tools"]["allow"])
        missing = REQUIRED_ALLOW - allow
        assert not missing, (
            f"tools.allow must include {sorted(REQUIRED_ALLOW)} per W1-P1 "
            f"task 1.2 (system-log, ens-db, knowledge, system_upgrade, db). "
            f"Missing: {sorted(missing)}"
        )

    def test_deny_contains_required_tools(self) -> None:
        meta = _load_meta()
        deny = set(meta["tools"]["deny"])
        missing = REQUIRED_DENY - deny
        assert not missing, (
            f"tools.deny must include {sorted(REQUIRED_DENY)} per W1-P1 task "
            f"1.2 / D20 (git_commit, edit_file, write_file, system_restart). "
            f"Missing: {sorted(missing)}"
        )

    def test_system_restart_in_deny_d20(self) -> None:
        """D20 — system_restart MUST be in tools.deny.

        The call-time refusal at upgrade_tools.py is env-conditional
        (``if self_env == 'live'``) and does NOT fire on dev/demo. The
        deny-list entry is the ONLY resolution-level strip that works
        on every install class (D20 approver-fix correction).
        """
        meta = _load_meta()
        assert "system_restart" in meta["tools"]["deny"], (
            "D20: tools.deny must contain 'system_restart' — this is the "
            "only resolution-level mechanism that survives dev/demo AND live."
        )


# =============================================================================
# 3. Agent discovery + registry (architect §4.3 — BASE + VERSIONED enumeration)
# =============================================================================


class TestAgentDiscovery:
    """AgentRegistry discovers maintenancer and surfaces its metadata."""

    def test_maintenancer_not_in_skip_dirs(self) -> None:
        from daemon.registry import SKIP_DIRS  # noqa: WPS433

        assert "maintenancer" not in SKIP_DIRS, (
            "'maintenancer' should NOT be in SKIP_DIRS — it is a real agent."
        )

    def test_registry_discovers_maintenancer(self) -> None:
        from daemon.registry import AgentRegistry  # noqa: WPS433

        agents_dir = MM_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        assert registry.exists("maintenancer"), (
            "'maintenancer' should be discoverable via AgentRegistry"
        )

    def test_get_resolved_returns_maintenancer(self) -> None:
        """W1-P1 task 1.11 #1 — get_resolved() returns the metadata."""
        from daemon.registry import AgentRegistry  # noqa: WPS433

        agents_dir = MM_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        meta = registry.get_resolved("maintenancer")
        assert meta is not None, (
            "registry.get_resolved('maintenancer') must return metadata"
        )
        assert meta.id == "maintenancer", f"id mismatch: {meta.id}"

    def test_get_version_returns_v1_metadata(self) -> None:
        """W1-P1 task 1.11 #3 — version: '1.0.0' resolves via get_version."""
        from daemon.registry import AgentRegistry  # noqa: WPS433

        agents_dir = MM_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        meta = registry.get_version("maintenancer")
        assert meta is not None, (
            "registry.get_version('maintenancer') must return metadata"
        )
        assert meta.version == "1.0.0", (
            f"version must be '1.0.0' (P1 task 1.2). Got: {meta.version!r}"
        )

    def test_versioned_meta_enumeration_extended(self) -> None:
        """Architect §4.3 EXTENDED — BASE + VERSIONED enumeration guard.

        Standing guard for future ``maintenancer[v2]``-class additions.
        Use ``get_version(...) or get_resolved(...)`` so the test
        continues to pass if a tagged version is added later.
        """
        from daemon.registry import AgentRegistry  # noqa: WPS433

        agents_dir = MM_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        # At least one of base or any tagged version must resolve.
        versions = registry.list_versions("maintenancer")
        assert versions, "list_versions('maintenancer') must return a non-empty list"

        resolved_any = False
        for v in versions:
            meta = registry.get_version("maintenancer", v)
            if meta is not None:
                resolved_any = True
                break
        if not resolved_any:
            meta = registry.get_resolved("maintenancer")
            resolved_any = meta is not None
        assert resolved_any, (
            "No versioned or base metadata resolves for 'maintenancer'"
        )

    def test_team_members_in_metadata(self) -> None:
        """W1-P1 task 1.11 #4 — team_members matches meta.json exactly."""
        from daemon.registry import AgentRegistry  # noqa: WPS433

        agents_dir = MM_AGENT_DIR.parent
        registry = AgentRegistry(agents_dir)
        registry.discover()

        meta = registry.get_resolved("maintenancer")
        assert meta is not None
        assert meta.team_members == ["explorer", "worker", "coder"], (
            f"team_members must be ['explorer', 'worker', 'coder'] "
            f"(OPEN B Option 1 — worker is system-log break-glass). "
            f"Got: {meta.team_members}"
        )


# =============================================================================
# 4. Convention compliance — prompt-file shape
# =============================================================================


class TestConventionCompliance:
    """Prompt files comply with the project's agent-prompt conventions."""

    def test_all_prompt_files_exist(self) -> None:
        """W1-P1 task 1.11 #5 — all 8 prompt files load (incl. skills-template/)."""
        missing: list[str] = []
        for p in PROMPT_FILES:
            if not p.exists():
                missing.append(str(p))
        if not SKILL_SET_PATH.exists():
            missing.append(str(SKILL_SET_PATH))
        if not SKILLS_TEMPLATE_DIR.exists():
            missing.append(str(SKILLS_TEMPLATE_DIR))
        assert not missing, f"Missing prompt files / directories: {missing}"

    @pytest.mark.parametrize("prompt_path", PROMPT_FILES, ids=lambda p: p.name)
    def test_prompt_file_non_empty(self, prompt_path: Path) -> None:
        text = _read(prompt_path)
        assert len(text) >= 100, (
            f"{prompt_path.name} is too short ({len(text)} chars)"
        )

    def test_skill_set_yaml_exists_and_empty_or_populated(self) -> None:
        """skill-set.yaml — empty placeholder (P1) or populated (P4)."""
        assert SKILL_SET_PATH.exists(), f"{SKILL_SET_PATH} missing"
        text = _read(SKILL_SET_PATH)
        assert "agent_id: maintenancer" in text, (
            "skill-set.yaml must declare agent_id: maintenancer"
        )

    def test_skills_template_dir_exists(self) -> None:
        assert SKILLS_TEMPLATE_DIR.is_dir(), (
            f"skills-template/ must be a directory (P4 populates)"
        )

    def test_knowledge_dir_exists(self) -> None:
        assert KNOWLEDGE_DIR.is_dir(), (
            f"knowledge/ must be a directory (P3 populates 6 KB docs)"
        )

    @pytest.mark.parametrize("prompt_path", PROMPT_FILES, ids=lambda p: p.name)
    def test_no_forbidden_system_tokens(self, prompt_path: Path) -> None:
        """W1-P1 task 1.11 #8 — forbidden-token grep returns zero hits."""
        text = _read(prompt_path)

        found: list[tuple[str, int]] = []
        for token in FORBIDDEN_TOKENS:
            for line_no, line in enumerate(text.splitlines(), start=1):
                if token in line:
                    found.append((token, line_no))

        assert not found, (
            f"{prompt_path.name} contains forbidden system tokens: "
            + ", ".join(f"{tok!r}@L{ln}" for tok, ln in found)
        )


# =============================================================================
# 5. Cardinal-rule count + workflow.md markers (writing guide §3, §7)
# =============================================================================


class TestRuleAndWorkflowMarkers:
    """Writing guide §3 (≤7 Cardinals) + §7 (END TURN, escape valve, report sanity)."""

    def test_cardinal_rules_at_most_seven(self) -> None:
        """W1-P1 task 1.11 #9 — Cardinal rules count ≤ 7."""
        rule_text = _read(RULE_PATH)
        cardinal_section = _extract_cardinal_section(rule_text)
        assert cardinal_section, (
            "rule.md must contain a '## Cardinal Rules' section"
        )
        cardinals = re.findall(r"^\s*\d+\.\s+", cardinal_section, flags=re.MULTILINE)
        assert len(cardinals) <= 7, (
            f"rule.md Cardinal Rules section must have ≤ 7 entries "
            f"(writing guide §3 — flat 30-rule lists dilute load-bearing "
            f"invariants). Got: {len(cardinals)}"
        )

    def test_workflow_has_end_turn_marker(self) -> None:
        """W1-P1 task 1.11 #10 — END TURN marker present in workflow.md."""
        text = _read(WORKFLOW_PATH)
        assert "END TURN" in text, (
            "workflow.md must contain the 'END TURN' marker (writing guide §7)"
        )

    def test_workflow_has_escape_valve_marker(self) -> None:
        """W1-P1 task 1.11 #10 — escape valve marker present in workflow.md."""
        text = _read(WORKFLOW_PATH)
        assert "escape valve" in text.lower(), (
            "workflow.md must contain 'escape valve' marker (writing guide §7 "
            "fan-in escape valve)"
        )

    def test_workflow_has_report_sanity_marker(self) -> None:
        """W1-P1 task 1.11 #10 — [REPORT SANITY: …] marker present in workflow.md."""
        text = _read(WORKFLOW_PATH)
        assert "REPORT SANITY" in text, (
            "workflow.md must contain the '[REPORT SANITY: …]' marker "
            "(writing guide §7 report-scrutiny contract)"
        )


# =============================================================================
# 6. Leader team_members includes "maintenancer" (W1-P1 task 1.9)
# =============================================================================


class TestLeaderTeamMembership:
    """leader/meta.json team_members must include 'maintenancer'."""

    def test_leader_team_members_includes_maintenancer(self) -> None:
        leader_meta = _load_leader_meta()
        team = leader_meta.get("team_members", [])
        assert "maintenancer" in team, (
            f"leader team_members must include 'maintenancer' (W1-P1 task 1.9). "
            f"Got: {team}"
        )

    def test_leader_team_members_length_14(self) -> None:
        """Spec — current length 13 → 14 after appending 'maintenancer'."""
        leader_meta = _load_leader_meta()
        team = leader_meta.get("team_members", [])
        assert len(team) == 14, (
            f"leader team_members length must be 14 after W1-P1 task 1.9 "
            f"(was 13, +1 for 'maintenancer'). Got: {len(team)}"
        )
