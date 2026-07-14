# Phase 3: Startup Seeding

## Objective

Implement a versioned, idempotent seeding mechanism that scans `agents/*/skill-set.md` at ensemble startup and populates the `skill_bank` table from `skills-template/` files. On subsequent startups, detects version bumps and refreshes bank content.

## Coupling

- **Depends on**: Phase 1 (template files), Phase 2 (schema: `template_version`, `agent_id`, `auto_load`)
- **Coupling type**: tight

## Context

### skill-set.md Format: YAML Frontmatter (W1 fix)

**Decision**: Use **YAML frontmatter** (not custom markdown format) for skill-set.md. This is consistent with the project's existing YAML usage (`daemon/config.py` uses `pyyaml`).

**Format**: A single markdown file with YAML frontmatter containing a `skills` list. Each skill entry has all metadata. The actual skill content lives in separate `skills-template/{name}.md` files.

### Category Convention (W2 fix)

All seeded skill_bank items use category `{agent_id}-skill-set` (e.g., `tester-skill-set`) to distinguish them from user-created bank items (which use `workflow`, `reference`, etc.).

## New Files

```
daemon/services/skill_seed_service.py     ← NEW
tests/unit/test_skill_seeding.py          ← NEW
```

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `SkillSeedService` | Core seeding logic | `daemon/services/skill_seed_service.py` (NEW) |
| 2 | Implement `parse_skill_set_file()` | Full YAML frontmatter parser | same |
| 3 | Implement `seed_agent()` | Per-agent seeding with version comparison | same |
| 4 | Implement `seed_all()` | Scan all agents/* directories | same |
| 5 | Wire into `EnsembleManager.initialize()` | Call after repos init | `daemon/manager.py:~1685` |
| 6 | Write unit tests | Idempotency, version bump, malformed YAML, missing template | `tests/unit/test_skill_seeding.py` (NEW) |

## Detailed Design

### 3.1 skill-set.md Format Specification

```yaml
---
# Tester Skill Set — defines the tester agent's evolvable skills.
# Each skill is seeded into the skill_bank at startup and cloned
# into project-scoped skills on first use.
#
# Template content lives in skills-template/{name}.md

skills:
  - name: test-strategy
    version: "1.0.0"
    auto_load: true
    category: planning
    description: "Blast radius assessment, test scope planning, parallelism strategy"

  - name: test-pack-execution
    version: "1.0.0"
    auto_load: true
    category: execution
    description: "Test pack creation, execution, TTQA optimization, timeout handling"

  - name: mock-test
    version: "1.0.0"
    auto_load: true
    category: execution
    description: "Mock service testing with 5-phase workflow and specification templates"

  - name: unit-test
    version: "1.0.0"
    auto_load: true
    category: execution
    description: "Unit test discovery, delegation, analysis, and fix workflow"

  - name: integration-test
    version: "1.0.0"
    auto_load: false
    category: execution
    description: "Cross-component testing, API boundary validation, contract testing"

  - name: e2e-test
    version: "1.0.0"
    auto_load: false
    category: execution
    description: "End-to-end full-flow testing, browser automation, user journeys"

  - name: ensure-validation
    version: "1.0.0"
    auto_load: false
    category: validation
    description: "ensure.md requirement validation across 4 phases"

  - name: flaky-test-management
    version: "1.0.0"
    auto_load: false
    category: maintenance
    description: "Flaky test detection, quarantine, auto-skip, and un-quarantine"

  - name: quick-fix
    version: "1.0.0"
    auto_load: false
    category: maintenance
    description: "Quick fix workflow for failures under 20 lines, with templates"
---
```

**Parsing contract**: The YAML frontmatter is delimited by `---` lines. The `skills` key contains a list of dicts, each with required keys: `name`, `version`, `auto_load`, `category`, `description`. The body after the frontmatter is ignored (documentation only).

### 3.2 Full Parser Implementation (C3 fix)

**File**: `daemon/services/skill_seed_service.py` (NEW)

```python
"""Startup seeding service for versioned skill templates.

Scans agents/*/skill-set.md at ensemble startup and populates
the skill_bank table from skills-template/ files. Idempotent:
on re-run, detects version bumps and refreshes bank content.

NOT gated by config.skill_evolution — the Skill Bank is
standalone infrastructure. Uses only SkillBankRepository.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from ..repositories.skill.skill_bank_repository import SkillBankRepository

logger = logging.getLogger(__name__)


@dataclass
class SkillSetEntry:
    """Parsed skill definition from skill-set.md frontmatter.
    
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
# YAML frontmatter is enclosed between two lines of exactly "---".
# The regex captures the YAML block between the first and second
# delimiter. It's non-greedy so it stops at the first closing "---".
_FRONTMATTER_RE = re.compile(
    r"^---\s*\n(.*?)\n---\s*\n",
    re.DOTALL,
)

# ── Required fields in each skill entry ──────────────────────────
_REQUIRED_FIELDS = frozenset({"name", "version", "auto_load", "category", "description"})


def parse_skill_set_file(skill_set_path: Path) -> list[SkillSetEntry]:
    """Parse a skill-set.md file into a list of SkillSetEntry objects.
    
    Reads YAML frontmatter delimited by --- lines. Expects a
    ``skills`` top-level key containing a list of skill definitions.
    
    Args:
        skill_set_path: Path to the skill-set.md file.
    
    Returns:
        List of SkillSetEntry objects. Empty list if the file has
        no skills or is malformed (malformed files log a warning
        and return empty rather than raising).
    
    Raises:
        FileNotFoundError: If skill_set_path does not exist.
    
    Example:
        >>> entries = parse_skill_set_file(Path("agents/tester/skill-set.md"))
        >>> entries[0].name
        'test-strategy'
        >>> entries[0].auto_load
        True
    """
    content = skill_set_path.read_text(encoding="utf-8")
    
    # Extract YAML frontmatter
    match = _FRONTMATTER_RE.match(content)
    if match is None:
        logger.warning(
            f"No YAML frontmatter found in {skill_set_path}. "
            f"Expected file to start with '---'. Skipping."
        )
        return []
    
    yaml_text = match.group(1)
    
    # Parse YAML
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
    """Seeds skill_bank from agents/*/skill-set.md + skills-template/.
    
    All methods are synchronous; callers bridge to async via
    asyncio.to_thread.
    """
    
    # Category convention (W2): seeded items use this suffix to
    # distinguish from user-created bank items.
    _CATEGORY_SUFFIX = "skill-set"
    
    def __init__(
        self,
        skill_bank_repo: SkillBankRepository,
        agents_dir: Path,
    ) -> None:
        self._bank_repo = skill_bank_repo
        self._agents_dir = agents_dir
    
    def seed_all(self) -> dict[str, int]:
        """Scan all agent directories and seed their skill sets.
        
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
            
            skill_set_path = agent_dir / "skill-set.md"
            if not skill_set_path.exists():
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
    
    def seed_agent(
        self,
        agent_id: str,
        agent_dir: Path,
        skill_set_path: Path,
    ) -> dict[str, int]:
        """Seed one agent's skill set into skill_bank.
        
        For each skill in skill-set.md:
        - If not in bank → INSERT (create)
        - If in bank with lower version → UPDATE content + version
        - If in bank with same/higher version → SKIP (W4: version guard)
        """
        summary = {"new": 0, "updated": 0, "unchanged": 0, "errors": 0}
        
        entries = parse_skill_set_file(skill_set_path)
        if not entries:
            logger.info(
                f"No skills to seed for agent '{agent_id}' "
                f"(empty or unparseable skill-set.md)"
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
```

### 3.3 Wiring into EnsembleManager.initialize()

**File**: `daemon/manager.py`, in `initialize()` (after trigger seed, ~line 1685)

```python
        # ── Skill Bank seeding (versioned templates) ───────────────────
        try:
            from pathlib import Path
            agents_base = Path(__file__).parent.parent / "agents"
            seed_service = SkillSeedService(
                skill_bank_repo=self._skill_bank_repo,
                agents_dir=agents_base,
            )
            seed_result = await asyncio.to_thread(seed_service.seed_all)
            if seed_result["new"] > 0 or seed_result["updated"] > 0:
                logger.info(f"Skill bank seeding: {seed_result}")
        except Exception as seed_exc:
            logger.warning(f"Skill bank seeding failed: {seed_exc}")
```

**Import** at top of manager.py:
```python
from .services.skill_seed_service import SkillSeedService
```

### 3.4 Version Guard Details (W4)

The version guard in `seed_agent()` ensures idempotency:

| Bank State | skill-set.md Version | Action |
|------------|---------------------|--------|
| Not in bank | any | INSERT (new) |
| v1.0.0 in bank | v1.0.0 | SKIP (unchanged) |
| v1.0.0 in bank | v1.1.0 | UPDATE (version bump) |
| v1.1.0 in bank | v1.0.0 | SKIP (bank is newer — manual update or downgrade) |
| v1.0.0 in bank | v2.0.0 | UPDATE (major version bump) |

The guard uses `_version_lt()` which handles standard semver (major.minor.patch) and falls back to string comparison for non-standard formats.

### 3.5 Error Handling Summary

| Error | Behavior |
|-------|----------|
| skill-set.md missing | Skip agent (no error) |
| skill-set.md has no frontmatter | Log warning, return empty list |
| YAML parse error | Log warning, return empty list |
| Missing `skills` key | Log warning, return empty list |
| Entry missing required field | Log warning, skip entry |
| Entry has wrong type | Log warning, skip entry |
| skills-template/{name}.md missing | Log warning, count as error |
| DB insert/update failure | Exception propagates to seed_all try/except |

## Key Files

- `daemon/services/skill_seed_service.py` — NEW (full implementation above)
- `daemon/manager.py:~1685` — initialize() hook
- `agents/tester/skill-set.md` — P1 creates this; P3 parses it

## Constraints

- Seeding runs on EVERY startup — must be fast (file read + DB upsert per skill)
- NOT gated by `skill_evolution` config — only needs `skill_bank_repo`
- Parser uses `yaml.safe_load()` (not `yaml.load()` — security)
- Category convention: `{agent_id}-skill-set` to distinguish from user items
- Must handle missing `skills-template/` directory gracefully

## Test Strategy

**File**: `tests/unit/test_skill_seeding.py` (NEW)

Test cases:
1. **parse_skill_set_file — valid file** → returns 9 SkillSetEntry objects with correct fields
2. **parse_skill_set_file — no frontmatter** → returns empty list + warning
3. **parse_skill_set_file — malformed YAML** → returns empty list + warning
4. **parse_skill_set_file — missing skills key** → returns empty list
5. **parse_skill_set_file — entry missing field** → that entry skipped, others returned
6. **parse_skill_set_file — entry with wrong type** → that entry skipped
7. **Fresh seed** — empty bank → 9 new, 0 updated, 0 unchanged
8. **Idempotent re-seed (W4)** — same version → 0 new, 0 updated, 9 unchanged
9. **Version bump (W4)** — bump one version → 1 updated
10. **Version downgrade (W4)** — lower version in skill-set.md → unchanged (bank is newer)
11. **Missing template file** → error counted, others seed
12. **Category convention (W2)** — bank items have category `tester-skill-set`
13. **auto_load stored correctly** — true/false values propagate to bank

Test fixtures: in-memory SQLite, temp directory with mock agent dirs + mock skill-set.md files.

## Deliverables

- [ ] `parse_skill_set_file()` fully implemented with YAML frontmatter parsing (C3)
- [ ] Handles all error cases gracefully (missing fields, malformed YAML, etc.)
- [ ] `seed_all()` scans agents/ and seeds skill_bank
- [ ] Seeding is idempotent with version guard (W4)
- [ ] Category convention `{agent_id}-skill-set` applied (W2)
- [ ] auto_load flag stored on bank items
- [ ] `EnsembleManager.initialize()` calls seeding
- [ ] Unit tests pass
