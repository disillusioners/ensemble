"""Unit tests for ``daemon.services.skill_seed_service``.

Phase 3 (Startup Seeding) test pack. Exercises:

* ``parse_skill_set_file()`` — YAML manifest parser (``.yaml`` primary,
  legacy ``.md`` frontmatter fallback)
* ``_version_lt()`` — semver comparison helper
* ``SkillSeedService.seed_agent()`` — per-agent seeding with version guard
* ``SkillSeedService.seed_all()`` — multi-agent scan with manifest
  discovery (``.yaml`` preferred, ``.md`` fallback)

Tests use an in-memory SQLite engine + ``SkillBankRepository`` and
build mock agent directories under ``tempfile.TemporaryDirectory``
to mimic the real ``agents/<name>/`` layout without touching the
production tree.

The 14 cases mirror the test strategy in
``.agents/shared/planning/tester-skill-evolution/phase3-plan.md``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from daemon.repositories.skill.skill_bank_repository import SkillBankRepository
from daemon.services.skill_seed_service import (
    SkillSeedService,
    SkillSetEntry,
    _version_lt,
    parse_skill_set_file,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def engine() -> Iterator[Engine]:
    """In-memory SQLite engine with the ``skill_bank`` table created."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def repository(engine: Engine) -> SkillBankRepository:
    """``SkillBankRepository`` wired to the in-memory engine."""
    return SkillBankRepository(engine)


def _write_skill_set_yaml(agent_dir: Path, yaml_body: str) -> Path:
    """Write a pure-YAML ``skill-set.yaml`` under ``agent_dir``.

    The primary manifest format is a plain ``.yaml`` file with no
    frontmatter delimiters. Use this for all current-format tests.

    Args:
        agent_dir: Agent directory (created if missing).
        yaml_body: YAML content written verbatim (must be pure YAML).

    Returns:
        Path to the written file.
    """
    agent_dir.mkdir(parents=True, exist_ok=True)
    skill_set = agent_dir / "skill-set.yaml"
    skill_set.write_text(yaml_body, encoding="utf-8")
    return skill_set


def _write_legacy_skill_set_md(
    agent_dir: Path,
    frontmatter_body: str,
    body_after: str = "\n# Body\n",
) -> Path:
    """Write a legacy ``skill-set.md`` (YAML frontmatter) for fallback tests.

    Args:
        agent_dir: Agent directory (created if missing).
        frontmatter_body: YAML body between the ``---`` delimiters.
        body_after: Markdown body after the frontmatter (ignored by
            the parser but realistic for real-world legacy files).

    Returns:
        Path to the written file.
    """
    agent_dir.mkdir(parents=True, exist_ok=True)
    skill_set = agent_dir / "skill-set.md"
    skill_set.write_text(
        f"---\n{frontmatter_body}\n---\n{body_after}",
        encoding="utf-8",
    )
    return skill_set


def _write_template(
    agent_dir: Path,
    skill_name: str,
    content: str = "# Template\ndo the thing.",
) -> Path:
    """Write a mock ``skills-template/{name}.md`` file and return the path."""
    template_dir = agent_dir / "skills-template"
    template_dir.mkdir(parents=True, exist_ok=True)
    template = template_dir / f"{skill_name}.md"
    template.write_text(content, encoding="utf-8")
    return template


# ============================================================================
# Helper: minimal valid YAML body
# ============================================================================

_VALID_FM_ONE = """\
skills:
  - name: alpha-skill
    version: "1.0.0"
    auto_load: true
    category: planning
    description: "Alpha planner"
"""

_VALID_FM_TWO = """\
skills:
  - name: alpha-skill
    version: "1.0.0"
    auto_load: true
    category: planning
    description: "Alpha planner"
  - name: beta-skill
    version: "1.0.0"
    auto_load: false
    category: execution
    description: "Beta executor"
"""


# ============================================================================
# 3.2 — parse_skill_set_file tests (cases 1-7)
# ============================================================================


class TestParseSkillSetFile:
    """YAML manifest parser behavior (.yaml primary, .md fallback)."""

    def test_valid_file_returns_entries_with_correct_fields(
        self, tmp_path: Path
    ) -> None:
        """Case 1: valid file → all entries parsed with correct fields."""
        agent_dir = tmp_path / "tester"
        _write_skill_set_yaml(agent_dir, _VALID_FM_TWO)

        entries = parse_skill_set_file(agent_dir / "skill-set.yaml")

        assert len(entries) == 2

        alpha, beta = entries
        assert isinstance(alpha, SkillSetEntry)
        assert alpha.name == "alpha-skill"
        assert alpha.version == "1.0.0"
        assert alpha.auto_load is True
        assert alpha.category == "planning"
        assert alpha.description == "Alpha planner"

        assert beta.name == "beta-skill"
        assert beta.auto_load is False
        assert beta.category == "execution"

    def test_no_frontmatter_returns_empty_list(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Case 2: legacy .md file without --- delimiters → [] + warning."""
        agent_dir = tmp_path / "a"
        agent_dir.mkdir()
        path = agent_dir / "skill-set.md"
        path.write_text(
            "# Just a heading, no frontmatter\n\nno skills here\n",
            encoding="utf-8",
        )

        with caplog.at_level("WARNING"):
            entries = parse_skill_set_file(path)

        assert entries == []
        assert any(
            "frontmatter" in rec.message.lower() for rec in caplog.records
        )

    def test_malformed_yaml_returns_empty(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Case 3: malformed YAML body → [] + warning, no raise."""
        agent_dir = tmp_path / "a"
        agent_dir.mkdir()
        path = agent_dir / "skill-set.yaml"
        path.write_text(
            "skills:\n  - name: 'unclosed\n",
            encoding="utf-8",
        )

        with caplog.at_level("WARNING"):
            entries = parse_skill_set_file(path)

        assert entries == []
        assert any(
            "malformed yaml" in rec.message.lower() for rec in caplog.records
        )

    def test_missing_skills_key_returns_empty(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Case 4: no 'skills' key → [] + warning."""
        agent_dir = tmp_path / "a"
        agent_dir.mkdir()
        path = agent_dir / "skill-set.yaml"
        path.write_text(
            "other_key: value\nfoo: bar\n",
            encoding="utf-8",
        )

        with caplog.at_level("WARNING"):
            entries = parse_skill_set_file(path)

        assert entries == []
        assert any("'skills'" in rec.message for rec in caplog.records)

    def test_entry_missing_required_field_skipped(
        self, tmp_path: Path
    ) -> None:
        """Case 5: entry with missing field → skipped, others kept."""
        agent_dir = tmp_path / "a"
        fm = """\
skills:
  - name: good-skill
    version: "1.0.0"
    auto_load: true
    category: planning
    description: "complete entry"
  - name: bad-skill
    version: "1.0.0"
    # auto_load missing
    category: planning
    description: "missing auto_load"
"""
        _write_skill_set_yaml(agent_dir, fm)

        entries = parse_skill_set_file(agent_dir / "skill-set.yaml")

        names = [e.name for e in entries]
        assert names == ["good-skill"]

    def test_entry_wrong_type_skipped(self, tmp_path: Path) -> None:
        """Case 6: entry not a dict → skipped, others kept."""
        agent_dir = tmp_path / "a"
        fm = """\
skills:
  - "this is a string, not a dict"
  - name: good-skill
    version: "1.0.0"
    auto_load: true
    category: planning
    description: "complete"
"""
        _write_skill_set_yaml(agent_dir, fm)

        entries = parse_skill_set_file(agent_dir / "skill-set.yaml")

        names = [e.name for e in entries]
        assert names == ["good-skill"]

    def test_empty_name_skipped(self, tmp_path: Path) -> None:
        """Case 7: entry with empty/whitespace name → skipped."""
        agent_dir = tmp_path / "a"
        fm = """\
skills:
  - name: "   "
    version: "1.0.0"
    auto_load: true
    category: planning
    description: "empty-name"
  - name: good
    version: "1.0.0"
    auto_load: true
    category: planning
    description: "real"
"""
        _write_skill_set_yaml(agent_dir, fm)

        entries = parse_skill_set_file(agent_dir / "skill-set.yaml")

        names = [e.name for e in entries]
        assert names == ["good"]

    def test_legacy_md_fallback_parses_frontmatter(
        self, tmp_path: Path
    ) -> None:
        """Backward-compat: legacy ``skill-set.md`` (with ``---`` delimiters)
        is still parsed correctly via the fallback path."""
        agent_dir = tmp_path / "a"
        _write_legacy_skill_set_md(agent_dir, _VALID_FM_TWO)

        entries = parse_skill_set_file(agent_dir / "skill-set.md")

        assert len(entries) == 2
        names = [e.name for e in entries]
        assert names == ["alpha-skill", "beta-skill"]
        alpha = entries[0]
        assert alpha.version == "1.0.0"
        assert alpha.auto_load is True
        assert alpha.category == "planning"
        assert alpha.description == "Alpha planner"


# ============================================================================
# 3.3 — _version_lt tests
# ============================================================================


class TestVersionLt:
    """Semver comparison helper."""

    def test_standard_semver(self) -> None:
        assert _version_lt("1.0.0", "1.0.1") is True
        assert _version_lt("1.0.1", "1.0.0") is False
        assert _version_lt("1.0.0", "1.0.0") is False
        assert _version_lt("1.0.0", "1.1.0") is True
        assert _version_lt("1.0.0", "2.0.0") is True

    def test_uneven_length(self) -> None:
        # Pad shorter with trailing zeros
        assert _version_lt("1.0", "1.0.1") is True
        assert _version_lt("1.0.1", "1.1") is True
        assert _version_lt("1.0", "1.0.0") is False

    def test_falls_back_to_string(self) -> None:
        # Non-numeric tail → graceful fallback to string comparison
        assert _version_lt("1.0.0-alpha", "1.0.0-beta") is True
        assert _version_lt("weird", "weirder") is True


# ============================================================================
# 3.4 — SkillSeedService tests (cases 7-13)
# ============================================================================


class TestSeedServiceFresh:
    """Case 7: fresh seed into empty bank creates all entries."""

    def test_fresh_seed_creates_all_entries(
        self, repository: SkillBankRepository, tmp_path: Path
    ) -> None:
        agent_dir = tmp_path / "tester"
        _write_skill_set_yaml(agent_dir, _VALID_FM_TWO)
        _write_template(agent_dir, "alpha-skill")
        _write_template(agent_dir, "beta-skill", "# Beta body")

        service = SkillSeedService(
            skill_bank_repo=repository, agents_dir=tmp_path
        )
        summary = service.seed_agent(
            agent_id="tester",
            agent_dir=agent_dir,
            skill_set_path=agent_dir / "skill-set.yaml",
        )

        assert summary["new"] == 2
        assert summary["updated"] == 0
        assert summary["unchanged"] == 0
        assert summary["errors"] == 0

        # Bank now has both rows
        alpha = repository.get_by_name_and_agent("alpha-skill", "tester")
        beta = repository.get_by_name_and_agent("beta-skill", "tester")
        assert alpha is not None
        assert beta is not None
        assert alpha.project_id is None
        assert beta.project_id is None
        assert alpha.category == "tester-skill-set"


class TestSeedServiceIdempotency:
    """Case 8: idempotent re-seed with same versions → all unchanged (W4)."""

    def test_idempotent_reseed_same_version(
        self, repository: SkillBankRepository, tmp_path: Path
    ) -> None:
        agent_dir = tmp_path / "tester"
        _write_skill_set_yaml(agent_dir, _VALID_FM_TWO)
        _write_template(agent_dir, "alpha-skill")
        _write_template(agent_dir, "beta-skill")

        service = SkillSeedService(
            skill_bank_repo=repository, agents_dir=tmp_path
        )

        # First run — everything new
        first = service.seed_agent(
            agent_id="tester",
            agent_dir=agent_dir,
            skill_set_path=agent_dir / "skill-set.yaml",
        )
        assert first["new"] == 2
        assert first["updated"] == 0
        assert first["unchanged"] == 0

        # Second run — no changes
        second = service.seed_agent(
            agent_id="tester",
            agent_dir=agent_dir,
            skill_set_path=agent_dir / "skill-set.yaml",
        )
        assert second["new"] == 0
        assert second["updated"] == 0
        assert second["unchanged"] == 2
        assert second["errors"] == 0


class TestSeedServiceVersionBump:
    """Cases 9 + 10: version bump and version downgrade behavior (W4)."""

    def test_version_bump_updates_content(
        self, repository: SkillBankRepository, tmp_path: Path
    ) -> None:
        """Case 9: bump one version → 1 updated."""
        agent_dir = tmp_path / "tester"
        _write_skill_set_yaml(agent_dir, _VALID_FM_TWO)
        _write_template(agent_dir, "alpha-skill", content="# Old\nv1.0.0")
        _write_template(agent_dir, "beta-skill", content="# Old\nv1.0.0")

        service = SkillSeedService(
            skill_bank_repo=repository, agents_dir=tmp_path
        )
        service.seed_agent(
            agent_id="tester",
            agent_dir=agent_dir,
            skill_set_path=agent_dir / "skill-set.yaml",
        )

        # Bump alpha only by rewriting skill-set.yaml
        bumped_fm = """\
skills:
  - name: alpha-skill
    version: "1.1.0"
    auto_load: true
    category: planning
    description: "Alpha planner v1.1"
  - name: beta-skill
    version: "1.0.0"
    auto_load: false
    category: execution
    description: "Beta executor"
"""
        _write_template(agent_dir, "alpha-skill", content="# New\nv1.1.0")
        _write_skill_set_yaml(agent_dir, bumped_fm)

        result = service.seed_agent(
            agent_id="tester",
            agent_dir=agent_dir,
            skill_set_path=agent_dir / "skill-set.yaml",
        )

        assert result["updated"] == 1
        assert result["unchanged"] == 1
        assert result["new"] == 0

        # Confirm alpha got new content & version
        alpha = repository.get_by_name_and_agent("alpha-skill", "tester")
        assert alpha is not None
        assert alpha.template_version == "1.1.0"
        assert "v1.1.0" in alpha.content
        assert alpha.description == "Alpha planner v1.1"

    def test_version_downgrade_keeps_newer_bank(
        self, repository: SkillBankRepository, tmp_path: Path
    ) -> None:
        """Case 10: lower version in skill-set.yaml → skip (bank is newer)."""
        agent_dir = tmp_path / "tester"
        _write_skill_set_yaml(agent_dir, _VALID_FM_TWO)
        _write_template(agent_dir, "alpha-skill")
        _write_template(agent_dir, "beta-skill")

        service = SkillSeedService(
            skill_bank_repo=repository, agents_dir=tmp_path
        )
        service.seed_agent(
            agent_id="tester",
            agent_dir=agent_dir,
            skill_set_path=agent_dir / "skill-set.yaml",
        )

        # Manually promote alpha in the bank to v2.0.0 (simulates manual edit)
        alpha = repository.get_by_name_and_agent("alpha-skill", "tester")
        assert alpha is not None
        repository.update(
            alpha.id, template_version="2.0.0", content="# Bank content v2.0"
        )

        # Rewrite skill-set.yaml to downgrade to v1.5.0
        downgraded_fm = """\
skills:
  - name: alpha-skill
    version: "1.5.0"
    auto_load: true
    category: planning
    description: "Alpha at v1.5"
  - name: beta-skill
    version: "1.0.0"
    auto_load: false
    category: execution
    description: "Beta executor"
"""
        _write_skill_set_yaml(agent_dir, downgraded_fm)

        result = service.seed_agent(
            agent_id="tester",
            agent_dir=agent_dir,
            skill_set_path=agent_dir / "skill-set.yaml",
        )

        # W4 guard: same-or-higher in bank → skip
        assert result["unchanged"] == 2
        assert result["updated"] == 0
        assert result["new"] == 0

        # Bank alpha still has v2.0.0 and the manually edited content
        alpha_after = repository.get_by_name_and_agent("alpha-skill", "tester")
        assert alpha_after is not None
        assert alpha_after.template_version == "2.0.0"
        assert "Bank content v2.0" in alpha_after.content


class TestSeedServiceMissingTemplate:
    """Case 11: missing template → error counted, others seed."""

    def test_missing_template_counts_error(
        self, repository: SkillBankRepository, tmp_path: Path
    ) -> None:
        agent_dir = tmp_path / "tester"
        _write_skill_set_yaml(agent_dir, _VALID_FM_TWO)

        # Only write one of the two templates
        _write_template(agent_dir, "alpha-skill")

        service = SkillSeedService(
            skill_bank_repo=repository, agents_dir=tmp_path
        )
        result = service.seed_agent(
            agent_id="tester",
            agent_dir=agent_dir,
            skill_set_path=agent_dir / "skill-set.yaml",
        )

        # alpha seeded, beta missing template → error
        assert result["new"] == 1
        assert result["errors"] == 1
        assert result["unchanged"] == 0
        assert result["updated"] == 0

        alpha = repository.get_by_name_and_agent("alpha-skill", "tester")
        beta = repository.get_by_name_and_agent("beta-skill", "tester")
        assert alpha is not None
        assert beta is None


class TestSeedServiceCategoryConvention:
    """Case 12: bank items use category ``{agent_id}-skill-set`` (W2)."""

    def test_category_uses_agent_id_suffix(
        self, repository: SkillBankRepository, tmp_path: Path
    ) -> None:
        agent_dir = tmp_path / "tester"
        _write_skill_set_yaml(agent_dir, _VALID_FM_ONE)
        _write_template(agent_dir, "alpha-skill")

        service = SkillSeedService(
            skill_bank_repo=repository, agents_dir=tmp_path
        )
        service.seed_agent(
            agent_id="tester",
            agent_dir=agent_dir,
            skill_set_path=agent_dir / "skill-set.yaml",
        )

        alpha = repository.get_by_name_and_agent("alpha-skill", "tester")
        assert alpha is not None
        assert alpha.category == "tester-skill-set"


class TestSeedServiceAutoLoad:
    """Case 13: ``auto_load`` flag propagates to bank row."""

    def test_auto_load_stored(
        self, repository: SkillBankRepository, tmp_path: Path
    ) -> None:
        agent_dir = tmp_path / "tester"
        _write_skill_set_yaml(agent_dir, _VALID_FM_TWO)
        _write_template(agent_dir, "alpha-skill")
        _write_template(agent_dir, "beta-skill")

        service = SkillSeedService(
            skill_bank_repo=repository, agents_dir=tmp_path
        )
        service.seed_agent(
            agent_id="tester",
            agent_dir=agent_dir,
            skill_set_path=agent_dir / "skill-set.yaml",
        )

        alpha = repository.get_by_name_and_agent("alpha-skill", "tester")
        beta = repository.get_by_name_and_agent("beta-skill", "tester")
        assert alpha is not None
        assert beta is not None
        assert alpha.auto_load is True
        assert beta.auto_load is False


# ============================================================================
# Bonus: seed_all() cross-agent scan
# ============================================================================


class TestSeedAll:
    """Bonus coverage for multi-agent ``seed_all()``."""

    def test_seed_all_scans_multiple_agents(
        self, repository: SkillBankRepository, tmp_path: Path
    ) -> None:
        """Two agents with distinct skill-sets → both seeded; _-dirs ignored."""
        agents_root = tmp_path / "agents"
        agents_root.mkdir()

        # Agent tester
        tester_dir = agents_root / "tester"
        _write_skill_set_yaml(tester_dir, _VALID_FM_TWO)
        _write_template(tester_dir, "alpha-skill")
        _write_template(tester_dir, "beta-skill")

        # Agent developer (one skill)
        dev_dir = agents_root / "developer"
        dev_fm = """\
skills:
  - name: refactor-skill
    version: "0.1.0"
    auto_load: true
    category: refactoring
    description: "Refactor like a pro"
"""
        _write_skill_set_yaml(dev_dir, dev_fm)
        _write_template(dev_dir, "refactor-skill")

        # A non-agent directory that should be ignored (underscore prefix)
        (agents_root / "_prompt_system").mkdir()
        (agents_root / "_prompt_system" / "skill-set.md").write_text(
            "---\nskills:\n  - name: ignored\n    version: 1.0\n"
            "    auto_load: true\n    category: x\n    description: 'x'\n"
            "---\n",
            encoding="utf-8",
        )

        service = SkillSeedService(
            skill_bank_repo=repository, agents_dir=agents_root
        )
        summary = service.seed_all()

        # 2 tester + 1 developer = 3 new
        assert summary["new"] == 3
        assert summary["updated"] == 0
        assert summary["unchanged"] == 0
        assert summary["errors"] == 0

        # Each agent has its own bank rows
        assert (
            repository.get_by_name_and_agent("alpha-skill", "tester") is not None
        )
        assert (
            repository.get_by_name_and_agent("beta-skill", "tester") is not None
        )
        assert (
            repository.get_by_name_and_agent("refactor-skill", "developer")
            is not None
        )
        # _prompt_system was ignored
        assert (
            repository.get_by_name_and_agent("ignored", "_prompt_system")
            is None
        )

    def test_seed_all_skips_agents_without_skill_set(
        self, repository: SkillBankRepository, tmp_path: Path
    ) -> None:
        """Agents without a skill-set.yaml are silently skipped."""
        agents_root = tmp_path / "agents"
        agents_root.mkdir()
        # Agent dir without skill-set.{yaml,md}
        (agents_root / "no_skill_set").mkdir()

        service = SkillSeedService(
            skill_bank_repo=repository, agents_dir=agents_root
        )
        summary = service.seed_all()

        assert summary["new"] == 0
        assert summary["errors"] == 0

    def test_seed_all_falls_back_to_legacy_md(
        self, repository: SkillBankRepository, tmp_path: Path
    ) -> None:
        """Agent with only legacy ``skill-set.md`` is still seeded via fallback."""
        agents_root = tmp_path / "agents"
        agents_root.mkdir()

        # Agent with legacy .md only (no .yaml)
        legacy_dir = agents_root / "legacy-agent"
        _write_legacy_skill_set_md(legacy_dir, _VALID_FM_ONE)
        _write_template(legacy_dir, "alpha-skill")

        service = SkillSeedService(
            skill_bank_repo=repository, agents_dir=agents_root
        )
        summary = service.seed_all()

        assert summary["new"] == 1
        assert summary["errors"] == 0
        alpha = repository.get_by_name_and_agent("alpha-skill", "legacy-agent")
        assert alpha is not None
        assert alpha.category == "legacy-agent-skill-set"

    def test_seed_all_prefers_yaml_over_md(
        self, repository: SkillBankRepository, tmp_path: Path
    ) -> None:
        """When both ``skill-set.yaml`` and ``skill-set.md`` exist, .yaml wins."""
        agents_root = tmp_path / "agents"
        agents_root.mkdir()

        agent_dir = agents_root / "dual-agent"

        # .md version defines alpha-skill v1.0.0 (legacy)
        legacy_md = """\
skills:
  - name: alpha-skill
    version: "1.0.0"
    auto_load: false
    category: legacy-cat
    description: "Legacy alpha"
"""
        _write_legacy_skill_set_md(agent_dir, legacy_md)

        # .yaml version defines alpha-skill v2.0.0 + an extra beta-skill
        modern_yaml = """\
skills:
  - name: alpha-skill
    version: "2.0.0"
    auto_load: true
    category: modern-cat
    description: "Modern alpha"
  - name: beta-skill
    version: "1.0.0"
    auto_load: false
    category: modern-cat
    description: "Modern beta"
"""
        (agent_dir / "skill-set.yaml").write_text(modern_yaml, encoding="utf-8")
        _write_template(agent_dir, "alpha-skill")
        _write_template(agent_dir, "beta-skill")

        service = SkillSeedService(
            skill_bank_repo=repository, agents_dir=agents_root
        )
        summary = service.seed_all()

        assert summary["new"] == 2
        assert summary["errors"] == 0

        # .yaml values must have won: alpha is v2.0.0 (not v1.0.0 from .md),
        # auto_load is True (not False from .md), and beta-skill (only in
        # .yaml) is present. Category is the bank convention
        # "{agent_id}-skill-set" regardless of source manifest field.
        alpha = repository.get_by_name_and_agent("alpha-skill", "dual-agent")
        assert alpha is not None
        assert alpha.template_version == "2.0.0"
        assert alpha.auto_load is True
        assert alpha.category == "dual-agent-skill-set"
        assert alpha.description == "Modern alpha"
        beta = repository.get_by_name_and_agent("beta-skill", "dual-agent")
        assert beta is not None
