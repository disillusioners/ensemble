"""Census pack for the Video Editor agent (video-team activation step 4).

Exact-equality pins over agents/video-editor/:
  1. meta.json schema + required fields (mirror test_maintenancer_agent.py).
  2. tools.allow EXACT set + ordering discipline (pok immediately before
     nest-report; nest-report LAST).
  3. team_members exact set.
  4. Registry discovery via AgentRegistry (the real agents/ dir).
  5. Prompt-file inventory (soul/rule/workflow/tools_note + skill-set.yaml
     + skills-template).

All tests are pure file + registry parsing — no daemon/DB startup, no LLM calls.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = PROJECT_ROOT / "agents" / "video-editor"
META_PATH = AGENT_DIR / "meta.json"

EXPECTED_ID = "video-editor"
EXPECTED_NAME = "Video Editor"
EXPECTED_ALLOW = ['bash', 'proc', 'filesystem', 'context', 'shared_meta_kv', 'todo', 'time', 'help', 'midflight', 'image', 'view-views', 'pok', 'nest-report']
EXPECTED_TEAM_MEMBERS = []
EXPECTED_INNATE_SKILLS = ["dynamic-skill", "todo"]
EXPECTED_TEMPLATES = ['video-exact-edit.md']


def _load_meta() -> dict:
    return json.loads(META_PATH.read_text(encoding="utf-8"))


class TestMetaJsonSchema:
    def test_meta_json_exists(self) -> None:
        assert META_PATH.exists(), "meta.json not found"

    def test_meta_json_is_valid_json(self) -> None:
        meta = _load_meta()
        assert isinstance(meta, dict)

    def test_required_fields_exist(self) -> None:
        meta = _load_meta()
        required = ["id", "name", "version", "innate_skills", "skill_injection",
                    "tools", "team_members"]
        missing = [f for f in required if f not in meta]
        assert not missing, f"Required fields missing: {missing}"

    def test_agent_id(self) -> None:
        assert _load_meta()["id"] == EXPECTED_ID

    def test_agent_name(self) -> None:
        assert _load_meta()["name"] == EXPECTED_NAME

    def test_version_is_semver_string(self) -> None:
        version = _load_meta().get("version")
        assert isinstance(version, str) and re.match(r"^\d+\.\d+\.\d+", version)

    def test_innate_skills_exact(self) -> None:
        assert _load_meta()["innate_skills"] == EXPECTED_INNATE_SKILLS

    def test_skill_injection_is_false(self) -> None:
        assert _load_meta()["skill_injection"] is False

    def test_snapshot_enabled_is_true(self) -> None:
        assert _load_meta().get("snapshot_enabled") is True

    def test_no_force_explore_is_true(self) -> None:
        assert _load_meta().get("no_force_explore") is True


class TestToolAllowContract:
    def test_allow_exact_set_and_order(self) -> None:
        allow = _load_meta()["tools"]["allow"]
        assert allow == EXPECTED_ALLOW, (
            f"tools.allow drifted — expected {EXPECTED_ALLOW}, got {allow}"
        )

    def test_pok_immediately_before_nest_report(self) -> None:
        allow = _load_meta()["tools"]["allow"]
        assert allow.index("pok") == allow.index("nest-report") - 1

    def test_nest_report_is_last(self) -> None:
        assert _load_meta()["tools"]["allow"][-1] == "nest-report"

    def test_grant_appears_once_each(self) -> None:
        allow = _load_meta()["tools"]["allow"]
        assert allow.count("pok") == 1 and allow.count("nest-report") == 1


class TestTeamMembers:
    def test_team_members_exact(self) -> None:
        assert _load_meta()["team_members"] == EXPECTED_TEAM_MEMBERS


class TestRegistryDiscovery:
    def test_registry_discovers_agent(self) -> None:
        from daemon.registry import AgentRegistry
        registry = AgentRegistry(PROJECT_ROOT / "agents")
        registry.discover()
        agent = registry.get(EXPECTED_ID)
        assert agent is not None, f"registry does not discover {EXPECTED_ID}"
        assert agent.id == EXPECTED_ID


class TestPromptFileInventory:
    def test_core_prompt_files_exist(self) -> None:
        for name in ("soul.md", "rule.md", "workflow.md", "tools_note.md"):
            assert (AGENT_DIR / name).is_file(), f"missing {name}"

    def test_skill_set_yaml_exists(self) -> None:
        assert (AGENT_DIR / "skill-set.yaml").is_file()

    def test_skills_template_inventory(self) -> None:
        actual = sorted(p.name for p in (AGENT_DIR / "skills-template").glob("*.md"))
        assert actual == sorted(EXPECTED_TEMPLATES), (
            f"skills-template drifted — expected {sorted(EXPECTED_TEMPLATES)}, got {actual}"
        )
