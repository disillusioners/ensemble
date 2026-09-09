"""Skill-version consistency tests for the maintenancer agent (W2-P4 task 4.7).

Validates that every skill in ``agents/maintenancer/skill-set.yaml`` is
declared with a ``version`` that matches the ``.md`` frontmatter in the
matching ``skills-template/`` file (writing guide §6 — frontmatter is
the source of truth; the manifest must match). Also asserts the
manifest set equals the template-set (no missing or extra skills).

Pure file + manifest parsing — no daemon/DB startup, no LLM calls.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Path constants — resolve against the REAL repo root.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = PROJECT_ROOT / "agents" / "maintenancer"
SKILL_SET_PATH = AGENT_DIR / "skill-set.yaml"
SKILLS_TEMPLATE_DIR = AGENT_DIR / "skills-template"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_manifest() -> dict:
    """Load the ``skill-set.yaml`` manifest as a dict."""
    with open(SKILL_SET_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    assert isinstance(data, dict), f"{SKILL_SET_PATH} root must be a mapping"
    return data


def _iter_template_files() -> list[Path]:
    """Return every ``skills-template/*.md`` file, sorted by name."""
    if not SKILLS_TEMPLATE_DIR.is_dir():
        return []
    return sorted(p for p in SKILLS_TEMPLATE_DIR.glob("*.md"))


def _read(path: Path) -> str:
    """Read a UTF-8 text file."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _extract_frontmatter_version(text: str) -> str | None:
    """Return the frontmatter ``version`` field, or None if absent.

    Frontmatter format:
        ---
        version: <value>
        ...
        ---
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        return None
    frontmatter = "\n".join(lines[1:end])
    m = re.search(r"^version:\s*(\S+)\s*$", frontmatter, re.MULTILINE)
    return m.group(1).strip('"').strip("'") if m else None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_skill_set_yaml_exists_and_has_expected_agent_id() -> None:
    """Manifest pin — prevents accidental drift of agent_id."""
    manifest = _load_manifest()
    assert manifest.get("agent_id") == "maintenancer", (
        f"skill-set.yaml agent_id must be 'maintenancer'; "
        f"got {manifest.get('agent_id')!r}"
    )


def test_manifest_set_equals_template_set() -> None:
    """Every skill in the manifest has a matching ``.md`` file in
    ``skills-template/``, and every ``.md`` file is listed in the
    manifest. Prevents drift between the manifest set and the
    filesystem set.
    """
    manifest = _load_manifest()
    manifest_names = {skill["name"] for skill in manifest["skills"]}
    template_names = {p.stem for p in _iter_template_files()}

    missing_in_manifest = template_names - manifest_names
    missing_on_disk = manifest_names - template_names
    assert not missing_in_manifest, (
        f"templates exist on disk but not in manifest: {sorted(missing_in_manifest)}"
    )
    assert not missing_on_disk, (
        f"manifest lists skills with no matching template file: "
        f"{sorted(missing_on_disk)}"
    )


@pytest.mark.parametrize(
    "skill",
    _load_manifest()["skills"],
    ids=lambda s: s["name"],
)
def test_manifest_skill_version_matches_frontmatter(skill: dict) -> None:
    """For every skill listed in the manifest, the ``.md`` frontmatter
    version must equal the manifest version (writing guide §6).
    """
    name = skill["name"]
    manifest_version = skill["version"]
    template_path = SKILLS_TEMPLATE_DIR / f"{name}.md"
    assert template_path.exists(), (
        f"manifest skill {name!r} has no matching template file at {template_path}"
    )
    text = _read(template_path)
    frontmatter_version = _extract_frontmatter_version(text)
    assert frontmatter_version is not None, (
        f"{template_path} frontmatter missing 'version' field"
    )
    assert frontmatter_version == manifest_version, (
        f"version mismatch for skill {name!r}: "
        f"manifest={manifest_version!r} vs frontmatter={frontmatter_version!r}"
    )


def test_kb_curator_only_auto_load_skill() -> None:
    """``kb-curator`` is the ONLY auto_load:true skill in the manifest
    (writing guide §6 + D4 Tier 3 first-turn RAG duty); all others
    are false. Prevents accidental auto-load of worker-loaded skills.
    """
    manifest = _load_manifest()
    auto_load_skills = [
        skill["name"] for skill in manifest["skills"] if skill.get("auto_load")
    ]
    assert auto_load_skills == ["kb-curator"], (
        f"only 'kb-curator' may have auto_load:true; got {auto_load_skills}"
    )


def test_seven_skills_in_manifest() -> None:
    """Spec task 4.1 — exactly 7 skills (log-forensics,
    job-mission-repair, ens-db-repair, restart-upgrade-ops,
    bug-advisory, health-check, kb-curator).
    """
    manifest = _load_manifest()
    assert len(manifest["skills"]) == 7, (
        f"maintenancer manifest must list exactly 7 skills; got {len(manifest['skills'])}"
    )
