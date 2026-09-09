"""team_members-within-team tests for the maintenancer agent (W2-P4 task 4.7).

Validates that every skill in ``agents/maintenancer/skills-template/``
holds its fallback references inside the agent's ``team_members``
(``explorer``, ``worker``, ``coder``) per writing guide §8 (the
org-chart rule for fallbacks). A skill that names a peer agent NOT in
``team_members`` references an unreachable peer — the spawn would
silently fail.

Pure file parsing — no daemon/DB startup, no LLM calls.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path constants — resolve against the REAL repo root.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = PROJECT_ROOT / "agents" / "maintenancer"
META_PATH = AGENT_DIR / "meta.json"
SKILLS_TEMPLATE_DIR = AGENT_DIR / "skills-template"


# ---------------------------------------------------------------------------
# Constants — agent names referenced in the canonical
# team_members list (per meta.json + spec task 4.5).
# ---------------------------------------------------------------------------
EXPECTED_TEAM_MEMBERS: frozenset[str] = frozenset({"explorer", "worker", "coder"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _read(path: Path) -> str:
    """Read a UTF-8 text file."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_meta() -> dict:
    """Load and return maintenancer/meta.json as a dict."""
    with open(META_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _iter_skill_files() -> list[Path]:
    """Return every ``skills-template/*.md`` file, sorted by name."""
    if not SKILLS_TEMPLATE_DIR.is_dir():
        return []
    return sorted(p for p in SKILLS_TEMPLATE_DIR.glob("*.md"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_meta_team_members_expectation_holds() -> None:
    """Sanity pin — the ``team_members`` we test against must match
    the meta.json values, so a future change to the operational
    team surfaces here first.
    """
    meta = _load_meta()
    actual = frozenset(meta["team_members"])
    assert actual == EXPECTED_TEAM_MEMBERS, (
        f"meta.json team_members drift: expected {sorted(EXPECTED_TEAM_MEMBERS)}; "
        f"got {sorted(actual)}"
    )


def test_skills_template_dir_exists() -> None:
    """skills-template/ must be a directory (per spec)."""
    assert SKILLS_TEMPLATE_DIR.is_dir(), (
        f"skills-template/ missing at {SKILLS_TEMPLATE_DIR}"
    )


@pytest.mark.parametrize(
    "skill_path",
    _iter_skill_files(),
    ids=lambda p: p.name,
)
def test_skill_does_not_name_out_of_team_fallback(skill_path: Path) -> None:
    """A skill that names a peer agent as a fallback MUST name one of
    ``team_members`` (``explorer``, ``worker``, ``coder``). Writing
    guide §8 — the org-chart rule for fallbacks.
    """
    text = _read(skill_path)
    # Find lines that mention a fallback — the most common shape is
    # "spawn a <peer>" or "fall back to a <peer>". We restrict the
    # check to lines that name a peer (one word starting lowercase)
    # as the fallback target.
    #
    # A negative hit means either:
    #  - the skill has no fallback at all (good), or
    #  - the skill names a fallback INSIDE team_members (good).
    #
    # A positive hit (peer not in EXPECTED_TEAM_MEMBERS) fails.
    fallback_pattern = re.compile(
        r"(?:fall\s+back\s+to\s+a|spawn\s+a|fall\s+back\s+to)\s+([a-z][a-z0-9_-]*)",
        re.IGNORECASE,
    )
    violations: list[tuple[str, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for m in fallback_pattern.finditer(line):
            peer = m.group(1).lower()
            if peer not in EXPECTED_TEAM_MEMBERS:
                violations.append((str(line_no), peer))
    assert not violations, (
        f"{skill_path.name}: skill names a fallback peer not in "
        f"team_members ({sorted(EXPECTED_TEAM_MEMBERS)}); the org-chart "
        f"rule (writing guide §8) requires the fallback to be reachable. "
        f"Offenders: {violations}"
    )
