"""Tests for the worker skill-set seed (P3-WP5) + strict registry flip.

Covers:

- **skill-set seed parse** — ``agents/worker/skill-set.yaml`` parses;
  the single ``install-opendesign`` entry carries its ``requires:``
  block with ``tools: [bash, instance]`` and EMPTY ``env`` and — per
  the PR4 resolution — NO ``mcp:`` key (requiring the not-yet-installed
  capability would deadlock the loader).
- **mandatory-first satisfied** — the skill body's first substantive
  line is ``capability_check(...)`` (rule: code fences skipped,
  headings NOT).
- **registry strict-load** — the production loader runs STRICT now
  that the skill exists: the real ``capabilities.yaml`` loads with
  ``pending=False``; a dangling ``installer_skill`` raises
  (fixture-based); the strict flag (not the happy path) is what makes
  dangling refs impossible.
- **worker anatomy** — ``agents/worker/meta.json`` carries ``infra``
  in ``tools.allow`` (kms_request/kms_attach reachability for the
  resumed original worker).
- **[resume] documentation pin** — the canonical convention spec lives
  in the install skill body.

Filesystem reads against the real repo tree (paths resolved from this
file's location); NO daemon, NO DB.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daemon.services.capability_resolver import (
    CapabilityRegistryError,
    RESUME_TAG,
    enforce_mandatory_first_instruction,
    load_production_capabilities_registry,
    parse_resume_message,
    parse_requires_block,
)
from daemon.services.skill_seed_service import SkillSeedService, parse_skill_set_file

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_DIR = REPO_ROOT / "agents"
WORKER_DIR = AGENTS_DIR / "worker"
SKILL_SET = WORKER_DIR / "skill-set.yaml"
SKILL_BODY = WORKER_DIR / "skills-template" / "install-opendesign.md"


class TestWorkerSkillSetParses:
    def test_skill_set_exists_and_parses(self):
        assert SKILL_SET.exists()
        entries = parse_skill_set_file(SKILL_SET)
        # P3-WP12: two worker skills — the installer (WP5) and the
        # consumer (WP12). Installer stays FIRST (index 0) by convention.
        assert len(entries) == 2
        entry = entries[0]
        assert entry.name == "install-opendesign"
        assert entry.version == "1.0.0"
        assert entry.auto_load is False
        assert entry.category == "execution"
        assert entry.description.strip()
        assert entries[1].name == "opendesign-verify"

    def test_requires_tools_and_env_only(self):
        entry = parse_skill_set_file(SKILL_SET)[0]
        assert entry.requirement is not None
        assert entry.requirement.tools == ["bash", "instance"]
        assert entry.requirement.env == []
        assert entry.requirement.mcp == []  # PR4: NO mcp key

    def test_requires_block_has_no_mcp_key_in_yaml(self):
        """PR4 deviation pin: the yaml itself must not declare mcp:."""
        import yaml

        raw = yaml.safe_load(SKILL_SET.read_text())
        entry = raw["skills"][0]
        assert "mcp" not in (entry.get("requires") or {})

    def test_requirement_is_nonempty_so_mandatory_first_applies(self):
        entry = parse_skill_set_file(SKILL_SET)[0]
        assert not entry.requirement.is_empty()

    def test_seed_agent_would_accept_the_template(self, tmp_path):
        """Run the REAL seed mechanics against the real worker dir with a
        throwaway bank — proves template exists, mandatory-first passes,
        and include resolution doesn't choke."""
        from daemon.repositories.skill.skill_bank_repository import (
            SkillBankRepository,
        )
        from sqlmodel import SQLModel, create_engine

        engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(engine)
        repo = SkillBankRepository(engine) if hasattr(SkillBankRepository, "__init__") else None
        if repo is None:
            pytest.skip("SkillBankRepository constructor shape changed")
        service = SkillSeedService(repo, AGENTS_DIR)
        result = service.seed_agent("worker", WORKER_DIR, SKILL_SET)
        assert result["errors"] == 0
        assert result["new"] == 2  # install-opendesign + opendesign-verify
        seeded = repo.get_by_name_and_agent("install-opendesign", "worker")
        assert seeded is not None
        assert "capability_check" in seeded.content[:200]
        verify_seeded = repo.get_by_name_and_agent("opendesign-verify", "worker")
        assert verify_seeded is not None
        assert "capability_check" in verify_seeded.content[:200]


class TestMandatoryFirstInstruction:
    def test_body_starts_with_capability_check(self):
        body = SKILL_BODY.read_text()
        # Mirror the enforcer's composition: strip front-matter FIRST,
        # then find the first substantive (non-blank, non-fence) line.
        from daemon.services.capability_resolver import (
            _first_substantive_line,
            _strip_frontmatter,
        )

        first = _first_substantive_line(_strip_frontmatter(body))
        assert first is not None
        assert first.strip().startswith("capability_check(")

    def test_enforcer_passes_with_parsed_requirement(self):
        entry = parse_skill_set_file(SKILL_SET)[0]
        # Must NOT raise MandatoryFirstInstructionError.
        enforce_mandatory_first_instruction(
            skill_name=entry.name,
            skill_body=SKILL_BODY.read_text(),
            requirement=entry.requirement,
            skill_set_path=str(SKILL_SET),
        )

    def test_body_has_no_heading_before_the_check(self):
        """The known failure mode: a markdown heading before the call
        violates the rule even when the call appears later."""
        body = SKILL_BODY.read_text()
        lines = body.splitlines()
        # Skip front-matter block if present.
        content = body
        if lines and lines[0].strip() == "---":
            for i in range(1, len(lines)):
                if lines[i].strip() == "---":
                    content = "\n".join(lines[i + 1:])
                    break
        for line in content.splitlines():
            if not line.strip():
                continue
            assert not line.lstrip().startswith("#"), (
                f"heading before capability_check: {line!r}"
            )
            break


class TestStrictRegistryLoad:
    def test_production_load_real_tree_strict_ok(self):
        entries = load_production_capabilities_registry()
        assert len(entries) == 1
        entry = entries[0]
        assert entry.capability_id == "opendesign"
        assert entry.installer_skill == "install-opendesign"
        assert entry.pending is False
        assert entry.schema_version == "0.16.1"

    def test_strict_load_rejects_dangling_installer(self, tmp_path):
        """With the strict flag, a dangling installer_skill is
        IMPOSSIBLE to load — the lazy-warning path is gone in
        production wiring."""
        agents = tmp_path / "agents" / "worker"
        agents.mkdir(parents=True)
        (agents / "skill-set.yaml").write_text(
            "agent_id: worker\n"
            "skills:\n"
            "  - name: some-other-skill\n"
            "    version: \"1.0.0\"\n"
            "    auto_load: false\n"
            "    category: x\n"
            "    description: d\n"
        )
        with pytest.raises(CapabilityRegistryError, match="install-opendesign"):
            load_production_capabilities_registry(agents_dir=agents.parent)

    def test_lazy_path_still_available_for_test_fixtures(self, tmp_path):
        """Tests keep their fixture-based non-strict surface: calling
        load_capabilities_registry DIRECTLY with lazy validation must
        still degrade to pending=True (only production wiring is strict)."""
        from daemon.services.capability_resolver import load_capabilities_registry

        registry = tmp_path / "capabilities.yaml"
        registry.write_text(
            "- capability_id: fixturecap\n"
            "  installer_skill: never-seeded-skill\n"
            "  builtin_mcp_class: Nope\n"
            "  schema_version: \"1\"\n"
            "  requires_secret: false\n"
            "  kms_service_id: \"\"\n"
        )
        entries = load_capabilities_registry(
            registry_path=registry,
            installer_skill_names={"some-real-skill"},  # non-empty, doesn't contain the ref
            lazy_installer_validation=True,  # non-strict test surface
        )
        assert entries[0].pending is True

    def test_yaml_pending_flag_flipped(self):
        import yaml

        registry_path = (
            AGENTS_DIR
            / "_prompt_system"
            / "innate-skills"
            / "dynamic-skill"
            / "capabilities.yaml"
        )
        raw = yaml.safe_load(registry_path.read_text())
        assert raw[0]["_pending"] is False


class TestWorkerAnatomy:
    def test_meta_json_tools_allow_has_infra(self):
        meta = json.loads((WORKER_DIR / "meta.json").read_text())
        assert "infra" in meta["tools"]["allow"]

    def test_meta_json_still_dynamic_skill_enabled(self):
        meta = json.loads((WORKER_DIR / "meta.json").read_text())
        assert meta["skill_injection"] is True
        assert "dynamic-skill" in meta["innate_skills"]

    def test_kms_tools_live_in_infra_category(self):
        """The reason infra was granted: kms_request/kms_attach register
        under the infra category."""
        from daemon.tools.infra import create_kms_tools  # noqa: F401 — import proves wiring
        import inspect

        src = inspect.getsource(create_kms_tools)
        assert 'register_tool_category("infra")' in src


class TestResumeConventionDocumentationPin:
    """WP11: the canonical spec lives in the install skill body; the
    consumer contract is documented alongside."""

    def test_body_documents_resume_tag(self):
        body = SKILL_BODY.read_text()
        assert RESUME_TAG in body
        for field in ("capability_id", "status", "tools_now_available", "resume_from"):
            assert field in body

    def test_body_documents_consumer_contract(self):
        body = SKILL_BODY.read_text()
        assert "Consumer contract" in body
        assert "capability_check" in body  # re-run rule
        assert "capability_missing" in body  # malformed → escalation path

    def test_parser_round_trips_the_documented_example(self):
        """The example block printed in the skill body must itself
        parse — documentation can't drift from the parser."""
        body = SKILL_BODY.read_text()
        start = body.index(f"{RESUME_TAG} {{")
        example = body[start : body.index("}", start) + 1]
        resume = parse_resume_message(example)
        assert resume.capability_id == "opendesign"
        assert resume.resume_from == "step_after_kms_bind"


class TestOpendesignVerifySkill:
    """P3-WP12 Step 2 — the consumer skill (the live cycle's trigger)."""

    @pytest.fixture
    def verify_entry(self):
        entries = parse_skill_set_file(SKILL_SET)
        return next(e for e in entries if e.name == "opendesign-verify")

    @pytest.fixture
    def verify_body(self):
        return (WORKER_DIR / "skills-template" / "opendesign-verify.md").read_text()

    def test_requires_declares_the_mcp_capability(self, verify_entry):
        """Unlike the installer (PR4: no mcp key — installing IS the
        fix), the consumer REQUIRES the capability it consumes."""
        req = verify_entry.requirement
        assert req is not None
        assert req.mcp == ["opendesign"]
        assert req.tools == ["bash"]
        assert not req.is_empty()

    def test_body_opens_with_capability_check(self, verify_body):
        from daemon.services.capability_resolver import (
            _first_substantive_line,
            _strip_frontmatter,
        )

        first = _first_substantive_line(_strip_frontmatter(verify_body))
        assert first is not None
        assert first.strip().startswith('capability_check("opendesign")')

    def test_enforcer_passes(self, verify_entry, verify_body):
        enforce_mandatory_first_instruction(
            skill_name=verify_entry.name,
            skill_body=verify_body,
            requirement=verify_entry.requirement,
            skill_set_path=str(SKILL_SET),
        )

    def test_body_emits_canonical_envelope_on_miss(self, verify_body):
        """The miss path pins the exact envelope fields the e2e test and
        the live cycle rely on."""
        assert "Result:" in verify_body
        assert '"capability_missing"' in verify_body
        assert '"installer_skill": "install-opendesign"' in verify_body
        assert "resume_hint" in verify_body

    def test_body_documents_resume_reentry(self, verify_body):
        assert RESUME_TAG in verify_body
        for field in ("capability_id", "status", "tools_now_available", "resume_from"):
            assert field in verify_body
        # Re-check rule: never trust the carried status.
        assert "parse_resume_message" in verify_body

    def test_resume_example_parses(self, verify_body):
        start = verify_body.index(f"{RESUME_TAG} {{")
        example = verify_body[start : verify_body.index("}", start) + 1]
        resume = parse_resume_message(example)
        assert resume.capability_id == "opendesign"
        assert resume.resume_from == "step_after_install"
