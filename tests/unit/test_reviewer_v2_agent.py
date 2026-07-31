"""Validation tests for the Reviewer [v2] agent.

Reviewer [v2] is a review-dispatcher agent (directory ``agents/reviewer[v2]/``)
that plans reviews, delegates analysis to skill-equipped worker instances, and
convenes the governor council for deep review. It is a read-only dispatcher:
it does NOT analyze code/plans itself and does NOT hold DB access.

These tests validate the agent's structural contract (meta.json), its skill
manifest (skill-set.yaml), its 6 skill-template frontmatters, and that the
agent registry discovers the tagged ``reviewer[v2]`` directory against the REAL
``agents/`` directory at the repo root.

All tests are pure file + registry parsing (in-memory) — no daemon/DB startup.
"""

import json
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Path constants — resolve against the REAL agents/ dir at repo root.
# ---------------------------------------------------------------------------

REVIEWER_V2_AGENT_DIR = (
    Path(__file__).resolve().parents[2] / "agents" / "reviewer[v2]"
)
REAL_AGENTS_DIR = REVIEWER_V2_AGENT_DIR.parent

# The 7 skill templates shipped with reviewer[v2].
SKILL_TEMPLATE_NAMES = [
    "review-strategy",
    "code-review",
    "plan-review",
    "architecture-review",
    "security-review",
    "pr-review",
    "business-logic-review",
]

# Expected allowed tool categories for reviewer[v2] (D3 council, W2 no-db).
EXPECTED_PRESENT_TOOL_CATEGORIES = [
    "instance",   # worker dispatch (D-dispatch)
    "council",    # convene_council (D3)
]
EXPECTED_ABSENT_TOOL_CATEGORIES = [
    "db",         # reviewer is read-only dispatcher (W2)
]

# Expected team members (base canonical ids).
EXPECTED_TEAM_MEMBERS = ["worker", "explorer", "governor"]


def _load_meta() -> dict:
    """Load reviewer[v2]/meta.json as a dict."""
    meta_path = REVIEWER_V2_AGENT_DIR / "meta.json"
    with open(meta_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a markdown file into (frontmatter_dict, body).

    Expects leading ``---`` fences. Raises ValueError if absent.
    """
    if not text.startswith("---"):
        raise ValueError("file does not start with a frontmatter fence '---'")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError("could not find closing '---' fence")
    return yaml.safe_load(parts[1]), parts[2].strip()


# =============================================================================
# 1. Agent discovery + directory layout
# =============================================================================


class TestReviewerV2Directory:
    """The reviewer[v2] directory and its core files exist on disk."""

    def test_agent_directory_exists(self) -> None:
        assert REVIEWER_V2_AGENT_DIR.exists(), (
            f"agents/reviewer[v2]/ directory not found at {REVIEWER_V2_AGENT_DIR}"
        )
        assert REVIEWER_V2_AGENT_DIR.is_dir()

    def test_meta_json_exists(self) -> None:
        meta_path = REVIEWER_V2_AGENT_DIR / "meta.json"
        assert meta_path.exists(), f"meta.json not found at {meta_path}"

    def test_skill_set_yaml_exists(self) -> None:
        skill_set_path = REVIEWER_V2_AGENT_DIR / "skill-set.yaml"
        assert skill_set_path.exists(), (
            f"skill-set.yaml not found at {skill_set_path}"
        )

    def test_skills_template_directory_exists(self) -> None:
        skills_dir = REVIEWER_V2_AGENT_DIR / "skills-template"
        assert skills_dir.exists() and skills_dir.is_dir()


# =============================================================================
# 2. meta.json structural validation
# =============================================================================


class TestReviewerV2MetaJson:
    """Validate reviewer[v2]/meta.json structure and key requirements."""

    def test_meta_json_is_valid_json(self) -> None:
        meta = _load_meta()
        assert isinstance(meta, dict)

    def test_required_fields_exist(self) -> None:
        meta = _load_meta()
        for field in ["id", "name", "description", "version", "tools", "team_members"]:
            assert field in meta, f"Required field '{field}' missing from meta.json"

    def test_agent_id_is_base_reviewer_not_composite(self) -> None:
        """D-core: meta.json id must be the BASE id 'reviewer', never the
        composite directory name 'reviewer[v2]'."""
        meta = _load_meta()
        assert meta.get("id") == "reviewer", (
            f"meta.json id should be base 'reviewer', got '{meta.get('id')!r}'"
        )

    def test_opencode_not_in_innate_skills(self) -> None:
        """D7: reviewer[v2] must NOT depend on opencode."""
        meta = _load_meta()
        innate = meta.get("innate_skills", [])
        assert "opencode" not in innate, (
            f"'opencode' must NOT be in innate_skills (D7). Got: {innate}"
        )

    def test_council_in_tools_allow(self) -> None:
        """D3: 'council' category must be allowed so convene_council works."""
        meta = _load_meta()
        allowed = meta.get("tools", {}).get("allow", [])
        assert "council" in allowed, (
            f"'council' must be in tools.allow (D3). Got: {allowed}"
        )

    def test_db_not_in_tools_allow(self) -> None:
        """W2: reviewer is a read-only dispatcher — no DB access."""
        meta = _load_meta()
        allowed = meta.get("tools", {}).get("allow", [])
        assert "db" not in allowed, (
            f"'db' must NOT be in tools.allow (W2 read-only). Got: {allowed}"
        )

    def test_instance_in_tools_allow(self) -> None:
        """Reviewer dispatches to worker instances — needs 'instance'."""
        meta = _load_meta()
        allowed = meta.get("tools", {}).get("allow", [])
        assert "instance" in allowed, (
            f"'instance' must be in tools.allow (worker dispatch). Got: {allowed}"
        )

    def test_skill_injection_enabled(self) -> None:
        meta = _load_meta()
        assert meta.get("skill_injection") is True, (
            f"skill_injection must be true. Got: {meta.get('skill_injection')!r}"
        )

    def test_context_injection_enabled(self) -> None:
        meta = _load_meta()
        # ``context_injection`` is now an object; the enabled flag is
        # ``heuristic_match_shared_md_files`` (see ADR for context_injection
        # object form).
        assert meta.get("context_injection") == {
            "heuristic_match_shared_md_files": True,
        }, (
            f"context_injection must be the new object form with "
            "heuristic_match_shared_md_files=true. "
            f"Got: {meta.get('context_injection')!r}"
        )

    def test_team_members_includes_worker_governor_explorer(self) -> None:
        meta = _load_meta()
        team = meta.get("team_members", [])
        assert isinstance(team, list)
        for member in EXPECTED_TEAM_MEMBERS:
            assert member in team, (
                f"team_members must include '{member}'. Got: {team}"
            )


# =============================================================================
# 3. skill-set.yaml manifest validation
# =============================================================================


class TestReviewerV2SkillSet:
    """Validate reviewer[v2]/skill-set.yaml manifest."""

    @staticmethod
    def _load_skill_set() -> dict:
        path = REVIEWER_V2_AGENT_DIR / "skill-set.yaml"
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def test_skill_set_is_valid_yaml(self) -> None:
        data = self._load_skill_set()
        assert isinstance(data, dict)

    def test_skill_set_agent_id_is_base_reviewer(self) -> None:
        data = self._load_skill_set()
        assert data.get("agent_id") == "reviewer", (
            f"skill-set.yaml agent_id must be base 'reviewer'. "
            f"Got: {data.get('agent_id')!r}"
        )

    def test_skill_set_registers_exactly_seven_skills(self) -> None:
        data = self._load_skill_set()
        skills = data.get("skills", [])
        assert isinstance(skills, list)
        assert len(skills) == 7, (
            f"skill-set.yaml must register exactly 7 skills. Got {len(skills)}: "
            f"{[s.get('name') for s in skills]}"
        )

    def test_review_strategy_is_auto_load_true(self) -> None:
        """D5: review-strategy is the reviewer's own planning skill — auto_load."""
        data = self._load_skill_set()
        skills = {s["name"]: s for s in data["skills"]}
        assert "review-strategy" in skills
        assert skills["review-strategy"].get("auto_load") is True, (
            "review-strategy must have auto_load: true (D5)"
        )

    def test_other_six_skills_are_auto_load_false(self) -> None:
        """The 6 execution skills are dispatched to workers, not auto-loaded."""
        data = self._load_skill_set()
        skills = {s["name"]: s for s in data["skills"]}
        execution_skills = [
            "code-review", "plan-review", "architecture-review",
            "security-review", "pr-review", "business-logic-review",
        ]
        for name in execution_skills:
            assert name in skills, f"skill '{name}' missing from skill-set.yaml"
            assert skills[name].get("auto_load") is False, (
                f"skill '{name}' must have auto_load: false (worker-dispatched)"
            )


# =============================================================================
# 4. Skill-template frontmatter validation
# =============================================================================


class TestReviewerV2SkillTemplates:
    """Validate the 7 skills-template/*.md frontmatters."""

    @pytest.mark.parametrize("skill_name", SKILL_TEMPLATE_NAMES)
    def test_skill_template_file_exists(self, skill_name: str) -> None:
        path = REVIEWER_V2_AGENT_DIR / "skills-template" / f"{skill_name}.md"
        assert path.exists(), f"skill template not found: {path}"

    @pytest.mark.parametrize("skill_name", SKILL_TEMPLATE_NAMES)
    def test_skill_template_frontmatter_parses(self, skill_name: str) -> None:
        path = REVIEWER_V2_AGENT_DIR / "skills-template" / f"{skill_name}.md"
        text = path.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(text)
        assert isinstance(fm, dict), f"{skill_name}: frontmatter not a dict"
        for field in ["version", "category", "auto_load"]:
            assert field in fm, f"{skill_name}: frontmatter missing '{field}'"
        assert body, f"{skill_name}: body is empty"

    def test_review_strategy_frontmatter_auto_load_true(self) -> None:
        path = REVIEWER_V2_AGENT_DIR / "skills-template" / "review-strategy.md"
        fm, _ = _parse_frontmatter(path.read_text(encoding="utf-8"))
        assert fm.get("auto_load") is True, (
            "review-strategy.md frontmatter auto_load must be true"
        )

    @pytest.mark.parametrize(
        "skill_name",
        ["code-review", "plan-review", "architecture-review",
         "security-review", "pr-review", "business-logic-review"],
    )
    def test_execution_skill_frontmatter_auto_load_false(
        self, skill_name: str
    ) -> None:
        path = REVIEWER_V2_AGENT_DIR / "skills-template" / f"{skill_name}.md"
        fm, _ = _parse_frontmatter(path.read_text(encoding="utf-8"))
        assert fm.get("auto_load") is False, (
            f"{skill_name}.md frontmatter auto_load must be false"
        )


# =============================================================================
# 5. Registry resolution against the REAL agents/ directory
# =============================================================================


class TestReviewerV2RegistryResolution:
    """Registry discovery + version resolution against the REAL agents/ dir.

    NOTE on the D16 invariant: the registry deliberately stores tagged
    variants in ``_versioned_agents`` (composite keys) and makes the legacy
    D16 lookup family (``get``, ``get_resolved``, ``resolve_to_id``,
    ``resolve_pure_id``) IGNORE composite keys — returning ``None`` — so that
    legacy spawn/restore paths never accidentally load a tagged prompt while
    believing they hold the base agent. The CORRECT way to resolve a tagged
    version is ``get_version(base_id, tag)``. These tests verify both the
    intentional None-for-composite behavior AND the working tag resolution.
    """

    @pytest.fixture(scope="class")
    def real_registry(self):
        from daemon.registry import AgentRegistry
        reg = AgentRegistry(REAL_AGENTS_DIR)
        reg.discover()
        return reg

    def test_reviewer_v2_discovered_in_versioned_agents(self, real_registry) -> None:
        """reviewer[v2] is stored under the composite key in _versioned_agents."""
        assert "reviewer[v2]" in real_registry._versioned_agents, (
            "reviewer[v2] must be discovered and stored in _versioned_agents"
        )
        # And NOT in _agents (plain keys only).
        assert "reviewer[v2]" not in real_registry._agents

    def test_reviewer_base_also_exists_separately(self, real_registry) -> None:
        """A plain (untagged) reviewer base entry also exists, separate from v2."""
        assert "reviewer" in real_registry._agents
        base = real_registry._agents["reviewer"]
        assert base.version_tag is None

    def test_resolve_to_id_ignores_composite_key(self, real_registry) -> None:
        """D16 invariant: resolve_to_id returns None for composite keys.

        This is intentional, NOT a bug — the D16 family (get, get_resolved,
        resolve_to_id, resolve_pure_id) deliberately ignores [tag] suffixes
        so legacy callers never silently load a tagged prompt. The correct
        tagged-resolution API is get_version(), tested below.
        """
        assert real_registry.resolve_to_id("reviewer[v2]") is None

    def test_get_version_resolves_to_base_id_with_v2_tag(self, real_registry) -> None:
        """The CORRECT API resolves reviewer[v2] to base id 'reviewer' + tag 'v2'."""
        resolved = real_registry.get_version("reviewer", "v2")
        assert resolved is not None
        assert resolved.id == "reviewer", (
            f"resolved id must be base 'reviewer', got {resolved.id!r}"
        )
        assert resolved.version_tag == "v2", (
            f"resolved version_tag must be 'v2', got {resolved.version_tag!r}"
        )

    def test_list_versions_records_base_and_v2(self, real_registry) -> None:
        """Available versions for 'reviewer' include the base (None) and 'v2'."""
        versions = real_registry.list_versions("reviewer")
        assert set(versions) == {None, "v2"}
