"""The FORBIDDEN LAYER gate (nest-ops §3.5, AC-17).

``docs/agent-prompt-writing-guide.md`` is BINDING for the agent-facing
prose this commission writes. The gate greps the two files the
commission touched for the guide's token list and requires ZERO hits
for the NEW prose.

BASELINE (recorded at HEAD ``8e6df58e4`` and unchanged by this
commission): ``agents/ari/workflow.md`` carries FIVE pre-existing
``agent_id=`` occurrences at its spawn-example lines — the plan (§3.5)
records them as PRE-EXISTING, out of scope, and a precedent NOT to
follow. This test pins the baseline so the pre-existing hits cannot
silently grow: the counts must stay exactly where they were.

``agents/maintenancer/tools_note.md`` is CLEAN today and must STAY
clean.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_TOKENS: tuple[str, ...] = (
    "meta.json",
    "tools.allow",
    "tools.deny",
    "daemon/",
    "_tool_registry",
    "skill-set.yaml",
    "agent_id=",
    "seed_all",
    "innate_skills",
    "default_agent_versions",
)

#: The PRE-EXISTING baseline per file (counted at HEAD before this
#: commission wrote a line). ``tools_note.md`` is clean; ``workflow.md``
#: carries the five pre-existing ``agent_id=`` spawn examples.
BASELINE_HITS: dict[str, int] = {
    "agents/maintenancer/tools_note.md": 0,
    "agents/ari/workflow.md": 5,
}

_PATTERN = re.compile("|".join(re.escape(tok) for tok in FORBIDDEN_TOKENS))


def _hits(path: Path) -> list[tuple[int, str]]:
    out = []
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if _PATTERN.search(line):
            out.append((lineno, line.strip()))
    return out


class TestForbiddenLayer:
    def test_maintenancer_tools_note_is_clean(self) -> None:
        hits = _hits(REPO_ROOT / "agents" / "maintenancer" / "tools_note.md")
        assert hits == [], f"forbidden-layer hits: {hits}"

    def test_ari_workflow_hits_do_not_exceed_the_preexisting_baseline(self) -> None:
        """The five pre-existing ``agent_id=`` spawn examples are out of
        scope; the commission adds NONE. The count must not grow."""
        path = REPO_ROOT / "agents" / "ari" / "workflow.md"
        hits = _hits(path)
        baseline = BASELINE_HITS["agents/ari/workflow.md"]
        assert len(hits) <= baseline, (
            f"agents/ari/workflow.md grew to {len(hits)} forbidden-layer "
            f"hits (baseline {baseline}): {hits}"
        )
        # and every surviving hit is one of the PRE-EXISTING spawn lines
        for lineno, line in hits:
            assert "agent_id=" in line, (
                f"non-baseline forbidden-layer hit at line {lineno}: {line}"
            )

    def test_the_new_ari_patrol_section_is_clean(self) -> None:
        """The nest-ops patrol section specifically carries ZERO hits."""
        source = (REPO_ROOT / "agents" / "ari" / "workflow.md").read_text(
            encoding="utf-8"
        )
        start = source.index("### Patrol loop — when an alarm wakes me")
        end = source.index("## [JOB_EVENT] Notification Parsing", start)
        section = source[start:end]
        assert _PATTERN.search(section) is None, (
            "the new patrol section contains a forbidden-layer token"
        )
