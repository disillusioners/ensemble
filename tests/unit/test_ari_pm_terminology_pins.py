"""Terminology pins for Ari's PM-tracking prompt sections (ari-pm-terminology).

User feedback (Discord 2026-11-06): "You don't need use the Plane word
everywhere, just give it a very concise note that Plane is backed
implement of PM, that's enough, use 'plan' word. Plan is more useful
instruction, Plane system is too specific."

The ari-plane-tracking doctrine (anchor commit ``5ab2009c0``) was
reworded implementation-agnostically — ZERO behavior change:

* ``agents/ari/rule.md`` — the three long-term-tracking policy bullets
  (inside the renamed ``### Project-Management Dispatch`` section) carry
  EXACTLY ONE case-insensitive ``plane`` occurrence: the concise backing
  note ("... currently backed by Plane — an implementation detail that
  may change ...").  Nothing else in the block names Plane — no
  case-variant dodges.
* ``agents/ari/workflow.md`` — the renamed "Commission Open & Close —
  the Project Ledger Habit" section carries ZERO ``plane`` occurrences;
  all references are generic ("the plan" / "the PM interface" /
  "PM work items" / "the project ledger").

The pins read the WORKTREE copies (resolved relative to THIS file —
never an absolute main-checkout path) and anchor section extraction on
stable concept tokens (the "Long-term tracking tiers" bullet lead; the
"Commission Open & Close" heading), robust to small wording edits.

Mutation legs (self-run during the lane — each must flip the pack RED):

* plant a second ``Plane`` in the rule.md tracking block → RED
* plant one ``Plane`` in the workflow.md ledger section → RED
* delete the backing note → RED
* restore → GREEN
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RULE_MD = REPO_ROOT / "agents" / "ari" / "rule.md"
WORKFLOW_MD = REPO_ROOT / "agents" / "ari" / "workflow.md"

#: The single sanctioned occurrence inside the rule.md tracking block.
_BACKING_NOTE_TOKEN = "backed by Plane"

#: Stable concept token opening the rule.md tracking block (first new
#: bullet's bold lead — the block's de-facto heading).
_TIERS_ANCHOR = "- **Long-term tracking tiers**"

#: Stable concept token opening the workflow.md ledger section (matched
#: as a line prefix — the em-dash suffix may evolve).
_WF_HEADING_ANCHOR = "## Commission Open & Close"


def _rule_tracking_block() -> str:
    """The three long-term-tracking policy bullets + the backing note.

    Bounded from the stable anchor to the section-closing ``---``
    horizontal rule (the block is the tail of its section, right before
    ``## Must Not``).
    """
    text = RULE_MD.read_text(encoding="utf-8")
    start = text.index(_TIERS_ANCHOR)
    end = text.index("\n---", start)
    return text[start:end]


def _workflow_ledger_section() -> str:
    """The Commission Open & Close section (heading → next top-level heading)."""
    text = WORKFLOW_MD.read_text(encoding="utf-8")
    start = text.index(_WF_HEADING_ANCHOR)
    end = text.index("\n## ", start)
    return text[start:end]


class TestAriPmTerminologyRuleSection:
    def test_tracking_block_resolves_non_vacuously(self):
        block = _rule_tracking_block()
        assert _TIERS_ANCHOR in block
        # all three policy bullets are inside the extraction window
        assert "**Link, don't duplicate**" in block
        assert "**Degradation fallback**" in block
        assert "the plan" in block

    def test_exactly_one_plane_occurrence_in_block(self):
        block = _rule_tracking_block()
        hits = re.findall(r"plane", block, flags=re.IGNORECASE)
        assert len(hits) == 1, (
            "the long-term-tracking block must carry EXACTLY ONE "
            f"'Plane' occurrence (the backing note); found {len(hits)}"
        )

    def test_backing_note_present_and_dismissive(self):
        block = _rule_tracking_block()
        assert _BACKING_NOTE_TOKEN in block, (
            "the concise Plane backing note is MISSING — without it the "
            "word has no anchor in the section at all"
        )
        assert "implementation" in block
        assert "never restated here" in block

    def test_section_heading_is_generic(self):
        text = RULE_MD.read_text(encoding="utf-8")
        assert "### Project-Management Dispatch" in text
        assert "Plane & Project-Management Dispatch" not in text


class TestAriPmTerminologyWorkflowSection:
    def test_ledger_section_resolves_non_vacuously(self):
        section = _workflow_ledger_section()
        assert section.startswith(_WF_HEADING_ANCHOR)
        # the four behavioral steps survive the reword (behavior-identical)
        assert "At dispatch" in section
        assert "At start" in section
        assert "At terminal milestones" in section
        assert "unavailable" in section
        assert "PM work item" in section

    def test_zero_plane_occurrences_in_section(self):
        section = _workflow_ledger_section()
        hits = re.findall(r"plane", section, flags=re.IGNORECASE)
        assert hits == [], (
            "the Commission Open & Close section must be Plane-free; "
            f"found {len(hits)} occurrence(s)"
        )

    def test_heading_is_generic(self):
        text = WORKFLOW_MD.read_text(encoding="utf-8")
        assert _WF_HEADING_ANCHOR in text
        assert "the Project Ledger Habit" in text
        assert "Plane Ledger Habit" not in text

    def test_generic_tool_boundary_sentence(self):
        # The generic equivalent of the old plane_* sentence: the concrete
        # tool naming lives with project-manager (covered by the backing
        # note in rule.md), never restated here.  Whitespace-normalized so
        # prose line-wrapping cannot dodge the assertion.
        section = _workflow_ledger_section()
        normalized = " ".join(section.split())
        assert "PM tool categories" in normalized
        assert "route through project-manager dispatches" in normalized
        assert "plane_" not in normalized.lower()
