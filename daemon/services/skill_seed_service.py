"""Startup seeding service for versioned skill templates.

Scans agents/*/skill-set.{yaml,md} at ensemble startup and populates
the skill_bank table from skills-template/ files. Idempotent:
on re-run, detects version bumps and refreshes bank content.

The primary manifest format is ``skill-set.yaml`` (pure YAML).
Legacy ``skill-set.md`` files with YAML frontmatter are still parsed
for backward compatibility with existing agents.

NOT gated by config.skill_evolution — the Skill Bank is
standalone infrastructure. Uses only SkillBankRepository.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..repositories.skill.skill_bank_repository import SkillBankRepository
from .skill_include_resolver import resolve_includes

logger = logging.getLogger(__name__)


@dataclass
class SkillSetEntry:
    """Parsed skill definition from skill-set.{yaml,md} manifest.

    Attributes:
        name: Skill name (matches skills-template/{name}.md filename).
        version: Semver version string (e.g. "1.0.0").
        auto_load: Whether this skill should be auto-loaded into
            the system prompt (true) or loaded on-demand (false).
        category: Free-form category for grouping (e.g. "planning",
            "execution", "validation", "maintenance").
        description: One-line human-readable summary.
    """

    name: str
    version: str
    auto_load: bool
    category: str
    description: str


# ── Frontmatter delimiter pattern ────────────────────────────────
# Used only for legacy skill-set.md files. YAML frontmatter is
# enclosed between two lines of exactly "---". The regex captures
# the YAML block between the first and second delimiter. It's
# non-greedy so it stops at the first closing "---".
_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n",
    re.DOTALL,
)

# ── Required fields in each skill entry ──────────────────────────
_REQUIRED_FIELDS = frozenset({"name", "version", "auto_load", "category", "description"})


def parse_skill_set_file(skill_set_path: Path) -> list[SkillSetEntry]:
    """Parse a skill-set.{yaml,md} file into a list of SkillSetEntry objects.

    For ``.yaml`` files: parses the whole file as YAML.
    For ``.md`` files (legacy): reads YAML frontmatter delimited by
    ``---`` lines. Both formats expect a ``skills`` top-level key
    containing a list of skill definitions.

    Args:
        skill_set_path: Path to the skill-set file. Extension determines
            parse mode (``.yaml`` → whole file; ``.md`` → frontmatter).

    Returns:
        List of SkillSetEntry objects. Empty list if the file has
        no skills or is malformed (malformed files log a warning
        and return empty rather than raising).

    Raises:
        FileNotFoundError: If skill_set_path does not exist.

    Example:
        >>> entries = parse_skill_set_file(Path("agents/tester/skill-set.yaml"))
        >>> entries[0].name
        'test-strategy'
        >>> entries[0].auto_load
        True
    """
    suffix = skill_set_path.suffix.lower()

    if suffix == ".yaml":
        try:
            data = yaml.safe_load(skill_set_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            logger.warning(
                f"Malformed YAML in {skill_set_path}: {e}. Skipping."
            )
            return []
    else:
        # Legacy .md: extract YAML frontmatter between --- delimiters.
        content = skill_set_path.read_text(encoding="utf-8")
        match = _FRONTMATTER_RE.match(content)
        if match is None:
            logger.warning(
                f"No YAML frontmatter found in {skill_set_path}. "
                f"Expected file to start with '---'. Skipping."
            )
            return []

        yaml_text = match.group(1)
        try:
            data = yaml.safe_load(yaml_text)
        except yaml.YAMLError as e:
            logger.warning(
                f"Malformed YAML in {skill_set_path}: {e}. Skipping."
            )
            return []

    if not isinstance(data, dict):
        logger.warning(
            f"Expected top-level dict in {skill_set_path}, "
            f"got {type(data).__name__}. Skipping."
        )
        return []

    skills_list = data.get("skills")
    if skills_list is None:
        logger.warning(
            f"No 'skills' key in {skill_set_path}. Skipping."
        )
        return []

    if not isinstance(skills_list, list):
        logger.warning(
            f"'skills' in {skill_set_path} is not a list "
            f"(got {type(skills_list).__name__}). Skipping."
        )
        return []

    # Parse each entry
    entries: list[SkillSetEntry] = []
    for i, raw_entry in enumerate(skills_list):
        if not isinstance(raw_entry, dict):
            logger.warning(
                f"Skill entry #{i} in {skill_set_path} is not a dict "
                f"(got {type(raw_entry).__name__}). Skipping entry."
            )
            continue

        # Check required fields
        missing = _REQUIRED_FIELDS - set(raw_entry.keys())
        if missing:
            logger.warning(
                f"Skill entry #{i} in {skill_set_path} missing required "
                f"fields: {missing}. Skipping entry."
            )
            continue

        try:
            entry = SkillSetEntry(
                name=str(raw_entry["name"]),
                version=str(raw_entry["version"]),
                auto_load=bool(raw_entry["auto_load"]),
                category=str(raw_entry["category"]),
                description=str(raw_entry["description"]),
            )
        except (ValueError, TypeError) as e:
            logger.warning(
                f"Failed to parse skill entry #{i} in {skill_set_path}: {e}. "
                f"Skipping entry."
            )
            continue

        # Validate name is non-empty
        if not entry.name.strip():
            logger.warning(
                f"Skill entry #{i} in {skill_set_path} has empty name. "
                f"Skipping entry."
            )
            continue

        entries.append(entry)

    return entries


def _version_lt(v1: str, v2: str) -> bool:
    """Compare semver strings. Returns True if v1 < v2.

    Handles "1.0.0" < "1.1.0" < "2.0.0" etc.
    Falls back to string comparison for non-standard versions.
    """
    try:
        parts1 = [int(x) for x in v1.split('.')]
        parts2 = [int(x) for x in v2.split('.')]
        # Pad to equal length
        while len(parts1) < len(parts2):
            parts1.append(0)
        while len(parts2) < len(parts1):
            parts2.append(0)
        return parts1 < parts2
    except (ValueError, AttributeError):
        return str(v1) < str(v2)


class SkillSeedService:
    """Seeds skill_bank from agents/*/skill-set.{yaml,md} + skills-template/.

    Primary manifest format is ``skill-set.yaml`` (pure YAML). Legacy
    ``skill-set.md`` files (with YAML frontmatter) are still supported
    as a fallback for backward compatibility.

    All methods are synchronous; callers bridge to async via
    asyncio.to_thread.
    """

    # Category convention (W2): seeded items use this suffix to
    # distinguish from user-created bank items.
    _CATEGORY_SUFFIX = "skill-set"

    # Primary manifest filename (modern, pure YAML). Legacy .md is
    # still supported as a fallback for backward compatibility.
    _MANIFEST_FILENAME = "skill-set.yaml"
    _LEGACY_MANIFEST_FILENAME = "skill-set.md"

    def __init__(
        self,
        skill_bank_repo: SkillBankRepository,
        agents_dir: Path,
    ) -> None:
        self._bank_repo = skill_bank_repo
        self._agents_dir = agents_dir

    def seed_all(self) -> dict[str, int]:
        """Scan all agent directories and seed their skill sets.

        For each agent directory, prefers ``skill-set.yaml`` and falls
        back to legacy ``skill-set.md`` (with YAML frontmatter).

        Returns:
            Summary: {"new": N, "updated": N, "unchanged": N, "errors": N}
        """
        summary = {"new": 0, "updated": 0, "unchanged": 0, "errors": 0}

        for agent_dir in sorted(self._agents_dir.iterdir()):
            if not agent_dir.is_dir():
                continue
            # Skip non-agent directories (e.g. _prompt_system)
            if agent_dir.name.startswith("_"):
                continue

            skill_set_path = self._find_manifest(agent_dir)
            if skill_set_path is None:
                continue

            agent_id = agent_dir.name
            try:
                result = self.seed_agent(agent_id, agent_dir, skill_set_path)
                for key in summary:
                    summary[key] += result.get(key, 0)
            except Exception as e:
                logger.warning(
                    f"Skill seeding failed for agent '{agent_id}': {e}"
                )
                summary["errors"] += 1

        logger.info(
            f"Skill seeding complete: {summary['new']} new, "
            f"{summary['updated']} updated, {summary['unchanged']} unchanged, "
            f"{summary['errors']} errors"
        )
        return summary

    @classmethod
    def _find_manifest(cls, agent_dir: Path) -> Path | None:
        """Locate the skill-set manifest for an agent directory.

        Prefers the modern ``skill-set.yaml`` (pure YAML). Falls back
        to the legacy ``skill-set.md`` (YAML frontmatter) when the
        YAML version does not exist. Returns ``None`` if neither is
        present.
        """
        yaml_path = agent_dir / cls._MANIFEST_FILENAME
        if yaml_path.exists():
            return yaml_path
        legacy_path = agent_dir / cls._LEGACY_MANIFEST_FILENAME
        if legacy_path.exists():
            return legacy_path
        return None

    def seed_agent(
        self,
        agent_id: str,
        agent_dir: Path,
        skill_set_path: Path,
    ) -> dict[str, int]:
        """Seed one agent's skill set into skill_bank.

        For each skill in skill-set.{yaml,md}:
        - If not in bank → INSERT (create)
        - If in bank with lower version → UPDATE content + version
        - If in bank with same/higher version → SKIP (W4: version guard)
        """
        summary = {"new": 0, "updated": 0, "unchanged": 0, "errors": 0}

        entries = parse_skill_set_file(skill_set_path)
        if not entries:
            logger.info(
                f"No skills to seed for agent '{agent_id}' "
                f"(empty or unparseable skill-set file: {skill_set_path.name})"
            )
            return summary

        templates_dir = agent_dir / "skills-template"
        bank_category = f"{agent_id}-{self._CATEGORY_SUFFIX}"

        for entry in entries:
            template_path = templates_dir / f"{entry.name}.md"
            if not template_path.exists():
                logger.warning(
                    f"Template not found: {template_path} "
                    f"(agent={agent_id}, skill={entry.name})"
                )
                summary["errors"] += 1
                continue

            template_content = template_path.read_text(encoding="utf-8")

            # Resolve ``include:`` directives from the template's
            # YAML frontmatter. The directive lets a skill inline
            # shared bodies (typically innate-skill invariants like
            # ``test-pack``'s 5-min cap / dual-layer timeout) so the
            # rendered ``skill_bank.content`` is self-contained under
            # the "one skill per send_message" design contract.
            # See :mod:`daemon.services.skill_include_resolver` for
            # the full resolution semantics (innate-skills first,
            # skill_bank fallback, cycle + depth guards).
            template_content = resolve_includes(
                template_content,
                agent_id=agent_id,
                agents_dir=self._agents_dir,
                bank_repo=self._bank_repo,
            )

            # Check if bank already has this template
            existing = self._bank_repo.get_by_name_and_agent(
                entry.name, agent_id
            )

            if existing is None:
                # New template — insert
                self._bank_repo.create(
                    name=entry.name,
                    content=template_content,
                    project_id=None,  # Templates are global
                    description=entry.description,
                    category=bank_category,
                    template_version=entry.version,
                    agent_id=agent_id,
                    auto_load=entry.auto_load,
                )
                summary["new"] += 1
                logger.debug(
                    f"Seeded new skill bank template: {entry.name} "
                    f"(agent={agent_id}, version={entry.version})"
                )
            elif _version_lt(existing.template_version, entry.version):
                # W4: Version guard — only update if template version
                # is strictly higher than the bank's stored version.
                # Same version = skip (idempotent). Lower version = skip
                # (bank has a newer version, probably manually updated).
                self._bank_repo.update(
                    existing.id,
                    content=template_content,
                    description=entry.description,
                    category=bank_category,
                    template_version=entry.version,
                    auto_load=entry.auto_load,
                )
                summary["updated"] += 1
                logger.info(
                    f"Updated skill bank template: {entry.name} "
                    f"(agent={agent_id}, {existing.template_version} → "
                    f"{entry.version})"
                )
            else:
                # Same or higher version in bank — skip (W4 guard)
                summary["unchanged"] += 1

        return summary
