"""Terminology pins for Ari's PM vocabulary (ari-pm-terminology + residual).

User feedback (Discord 2026-11-06): "You don't need use the Plane word
everywhere, just give it a very concise note that Plane is backed
implement of PM, that's enough, use 'plan' word. Plan is more useful
instruction, Plane system is too specific."

Authority (project metadata ``ari_plane_tracking_policy``):
``terminology_principle`` = "PROMPT-LEVEL GENERIC; IMPLEMENTATION NAMED
ONCE".  Ari's prompts speak generic vocabulary ("the plan", "the PM
interface", "PM operations", "the project ledger"); the concrete
implementation is named EXACTLY ONCE as a concise backing note in
``agents/ari/rule.md``; product specifics (workspace slugs, tool verbs,
resolver details) are NEVER restated in Ari's prompts.

Two waves landed this, both ZERO behavior change:

* **ari-pm-terminology** — the new behavioural sections were written
  generically from the start (``### Project-Management Dispatch``; the
  ``## Commission Open & Close — the Project Ledger Habit`` section).
* **ari-pm-terminology-residual** — the PRE-EXISTING residual mentions
  elsewhere in Ari's prompts were genericized too: the rule.md Default
  Delegation Targets row and the four dispatch bullets (Trigger /
  Topology / Scope split / Environment discipline), the workflow.md
  Triage Decision Matrix row (+ its example quote), and the soul.md
  Mode 3 sentence.  The backticked implementation verb names
  (``plane`` / ``plane_sync`` / ``plane_delete_issue``) went with them —
  product specifics are exactly what the principle forbids.

The invariant is now **WHOLE-FILE**, not section-scoped — a residual
mention anywhere in an Ari prompt is the same defect as one in the
behavioural section:

* ``agents/ari/rule.md`` — EXACTLY ONE case-insensitive ``plane``
  occurrence, and it MUST be the backing note ("... currently backed by
  Plane — an implementation detail that may change ...").
* ``agents/ari/workflow.md`` — ZERO.
* ``agents/ari/soul.md`` — ZERO.

``plan`` / ``planning`` / ``plan-ledger`` are unaffected; the pins match
the whole token ``plane`` case-insensitively.

The pins read the WORKTREE copies (resolved relative to THIS file —
never an absolute main-checkout path).  Section-scoped pins keep their
stable concept-token anchors (the "Long-term tracking tiers" bullet
lead; the "Commission Open & Close" heading) so they survive small
wording edits; the whole-file pins use :func:`_whole_file_violations`, a
PURE checker over a ``{relpath: text}`` mapping — which is what makes
the mutation legs below real tests rather than comments.

Mutation legs (each plants ONE defect into an in-memory copy and proves
the checker REDs — no repo bytes are touched):

* M1 — a second ``Plane`` anywhere in rule.md → RED
* M2 — a second ``Plane`` inside the rule.md tracking block → RED
* M3 — one ``Plane`` in workflow.md → RED
* M4 — one ``Plane`` in soul.md → RED
* M5 — the backing note deleted (zero occurrences in rule.md) → RED
* M6 — case variants (``plane`` / ``PLANE`` / ``pLaNe``) → RED
* M7 — the clean tree → GREEN (non-vacuity control)
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RULE_MD = REPO_ROOT / "agents" / "ari" / "rule.md"
WORKFLOW_MD = REPO_ROOT / "agents" / "ari" / "workflow.md"
SOUL_MD = REPO_ROOT / "agents" / "ari" / "soul.md"

#: The single sanctioned occurrence inside the rule.md tracking block.
_BACKING_NOTE_TOKEN = "backed by Plane"

#: Stable concept token opening the rule.md tracking block (first new
#: bullet's bold lead — the block's de-facto heading).
_TIERS_ANCHOR = "- **Long-term tracking tiers**"

#: Stable concept token opening the workflow.md ledger section (matched
#: as a line prefix — the em-dash suffix may evolve).
_WF_HEADING_ANCHOR = "## Commission Open & Close"

#: Ari's prompt files under the WHOLE-FILE gate, with the occurrence count
#: each must carry.  ``rule.md`` keeps ONE — the sanctioned backing note;
#: every other Ari prompt is Plane-free.  Adding a file here is how a new
#: Ari prompt joins the gate.
_WHOLE_FILE_EXPECTED: dict[str, int] = {
    "agents/ari/rule.md": 1,
    "agents/ari/workflow.md": 0,
    "agents/ari/soul.md": 0,
}

#: Paths as the tests address them (repo-relative → absolute).
_PROMPT_FILES: dict[str, Path] = {
    "agents/ari/rule.md": RULE_MD,
    "agents/ari/workflow.md": WORKFLOW_MD,
    "agents/ari/soul.md": SOUL_MD,
}

_PLANE_RE = re.compile(r"plane", re.IGNORECASE)


def _plane_hits(text: str) -> list[str]:
    """Every case-insensitive ``plane`` occurrence in ``text``."""
    return _PLANE_RE.findall(text)


def _read_all(texts: dict[str, str] | None = None) -> dict[str, str]:
    """The three Ari prompt bodies, or the caller's in-memory stand-ins."""
    if texts is not None:
        return texts
    return {
        rel: path.read_text(encoding="utf-8") for rel, path in _PROMPT_FILES.items()
    }


def _whole_file_violations(texts: dict[str, str] | None = None) -> list[str]:
    """PURE checker: every WHOLE-FILE terminology violation, as messages.

    Returns ``[]`` on a clean tree.  Deliberately pure over a
    ``{relpath: text}`` mapping so the mutation legs can plant a defect
    into an in-memory copy and prove the checker catches it — an
    assertion that can never fail is not a pin.
    """
    bodies = _read_all(texts)
    problems: list[str] = []

    for rel, expected in _WHOLE_FILE_EXPECTED.items():
        hits = _plane_hits(bodies[rel])
        if len(hits) != expected:
            problems.append(
                f"{rel}: {len(hits)} case-insensitive 'plane' occurrence(s), "
                f"expected {expected}"
            )

    rule = bodies["agents/ari/rule.md"]
    if _BACKING_NOTE_TOKEN not in rule:
        problems.append(
            "agents/ari/rule.md: the sanctioned backing note is MISSING — "
            "without it the word has no anchor in Ari's prompts at all"
        )
    for lineno, line in enumerate(rule.splitlines(), start=1):
        if _PLANE_RE.search(line) and _BACKING_NOTE_TOKEN not in line:
            problems.append(
                f"agents/ari/rule.md:{lineno} carries an occurrence OUTSIDE "
                f"the backing note: {line.strip()!r}"
            )

    return problems


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


# ---------------------------------------------------------------------------
# WHOLE-FILE gates (ari-pm-terminology-residual)
# ---------------------------------------------------------------------------
#
# The v0.28.16 wave pinned only the NEW behavioural sections.  The residual
# wave proves the invariant is file-wide: a product mention anywhere in an
# Ari prompt is the same defect as one in a behavioural section, and the
# section-scoped pins above cannot see it.


class TestWholeFileOccurrenceGate:
    """``plane`` (case-insensitive) — rule.md 1 (the backing note), others 0."""

    def test_the_clean_tree_passes_the_whole_file_gate(self):
        """Non-vacuity control: the real tree satisfies the checker."""
        assert _whole_file_violations() == []

    def test_rule_md_carries_exactly_one_occurrence(self):
        text = RULE_MD.read_text(encoding="utf-8")
        hits = _plane_hits(text)
        assert len(hits) == 1, (
            "agents/ari/rule.md must carry EXACTLY ONE case-insensitive "
            f"'plane' occurrence (the backing note); found {len(hits)}: {hits}"
        )

    def test_that_one_occurrence_is_the_backing_note(self):
        text = RULE_MD.read_text(encoding="utf-8")
        carrying = [
            (i, line)
            for i, line in enumerate(text.splitlines(), start=1)
            if _PLANE_RE.search(line)
        ]
        assert len(carrying) == 1, f"expected one carrying line, got {carrying}"
        lineno, line = carrying[0]
        assert _BACKING_NOTE_TOKEN in line, (
            f"agents/ari/rule.md:{lineno} carries the surviving occurrence but "
            f"it is NOT the backing note: {line.strip()!r}"
        )
        # the note stays a DISMISSIVE aside, not a reinstatement of the product
        assert "implementation" in text
        assert "never restated here" in text

    def test_workflow_md_is_plane_free(self):
        text = WORKFLOW_MD.read_text(encoding="utf-8")
        hits = _plane_hits(text)
        assert hits == [], (
            "agents/ari/workflow.md must be Plane-free; "
            f"found {len(hits)} occurrence(s)"
        )

    def test_soul_md_is_plane_free(self):
        text = SOUL_MD.read_text(encoding="utf-8")
        hits = _plane_hits(text)
        assert hits == [], (
            "agents/ari/soul.md must be Plane-free; "
            f"found {len(hits)} occurrence(s)"
        )

    def test_generic_vocabulary_is_actually_present(self):
        """A file cannot pass by being EMPTY of vocabulary — the generic
        replacements must carry the meaning the product words used to."""
        rule = RULE_MD.read_text(encoding="utf-8")
        workflow = WORKFLOW_MD.read_text(encoding="utf-8")
        soul = SOUL_MD.read_text(encoding="utf-8")

        # rule.md — the delegation row + the four dispatch bullets
        assert "PM operations" in rule
        assert "PM tool surface" in rule
        assert "PM sync" in rule
        assert "PM API keys and workspaces" in rule
        assert "PM-side" in rule
        assert "sync-internal" in rule
        # the backticked implementation verb names are GONE
        for verb in ("`plane`", "`plane_sync`", "`plane_delete_issue`"):
            assert verb not in rule, (
                f"agents/ari/rule.md still names the implementation verb {verb}"
            )

        # workflow.md — the triage row + its example quote
        assert "PM operations / project-management coordination" in workflow
        assert "the PM interface" in workflow or "PM work item" in workflow

        # soul.md — the Mode 3 sentence
        assert "PM work bypasses the mode ladder" in soul


class TestWholeFileMutationLegs:
    """Each leg plants ONE defect into an IN-MEMORY copy and proves the
    whole-file checker REDs.  No repo bytes are touched (the checker is
    pure over a ``{relpath: text}`` mapping), so these run on every
    invocation instead of being a one-off lane ritual."""

    @staticmethod
    def _clean() -> dict[str, str]:
        return _read_all()

    def test_M1_second_occurrence_anywhere_in_rule_md_goes_red(self):
        texts = self._clean()
        # appended at the very END of the file — nowhere near the backing
        # note or any behavioural section, i.e. the far corner of the file
        texts["agents/ari/rule.md"] += "\n<!-- Plane -->\n"
        problems = _whole_file_violations(texts)
        assert problems, "a second rule.md occurrence was NOT caught"
        assert any("agents/ari/rule.md: 2" in prob for prob in problems), problems

    def test_M2_second_occurrence_in_the_tracking_block_goes_red(self):
        texts = self._clean()
        texts["agents/ari/rule.md"] = texts["agents/ari/rule.md"].replace(
            "- **Link, don't duplicate**",
            "- **Link, don't duplicate** (Plane board stays separate)",
            1,
        )
        assert _whole_file_violations(texts), (
            "a second occurrence inside the tracking block was NOT caught"
        )

    def test_M3_one_occurrence_in_workflow_md_goes_red(self):
        texts = self._clean()
        texts["agents/ari/workflow.md"] += "\nPlane\n"
        problems = _whole_file_violations(texts)
        assert problems, "a workflow.md occurrence was NOT caught"
        assert any("agents/ari/workflow.md" in prob for prob in problems), problems

    def test_M4_one_occurrence_in_soul_md_goes_red(self):
        texts = self._clean()
        texts["agents/ari/soul.md"] += "\nPlane\n"
        problems = _whole_file_violations(texts)
        assert problems, "a soul.md occurrence was NOT caught"
        assert any("agents/ari/soul.md" in prob for prob in problems), problems

    def test_M5_deleting_the_backing_note_goes_red(self):
        texts = self._clean()
        rule = texts["agents/ari/rule.md"]
        texts["agents/ari/rule.md"] = rule.replace(
            _BACKING_NOTE_TOKEN, "backed by X", 1
        )
        problems = _whole_file_violations(texts)
        assert problems, "a deleted backing note was NOT caught"
        assert any("backing note is MISSING" in prob for prob in problems), problems

    def test_M5b_renaming_the_product_in_the_note_also_goes_red(self):
        """The note names the implementation ONCE — deleting the NAME but
        keeping the sentence shape still leaves the gate unsatisfied (the
        occurrence count drops to zero)."""
        texts = self._clean()
        texts["agents/ari/rule.md"] = texts["agents/ari/rule.md"].replace(
            "backed by Plane", "backed by the vendor", 1
        )
        problems = _whole_file_violations(texts)
        assert any(": 0 case-insensitive" in prob for prob in problems), problems

    def test_M6_case_variants_go_red(self):
        for variant in ("plane", "PLANE", "pLaNe"):
            texts = self._clean()
            # rule.md: a second hit in a non-note line
            texts["agents/ari/rule.md"] += f"\n{variant} project\n"
            assert _whole_file_violations(texts), (
                f"case variant {variant!r} in rule.md was NOT caught"
            )
            # soul.md: zero tolerance regardless of case
            texts = self._clean()
            texts["agents/ari/soul.md"] += f"\n{variant} work\n"
            assert _whole_file_violations(texts), (
                f"case variant {variant!r} in soul.md was NOT caught"
            )

    def test_M7_clean_tree_stays_green(self):
        """The control leg — proves the REDs above are the planted defect,
        not a checker that always fires."""
        assert _whole_file_violations(self._clean()) == []

    def test_plan_and_planning_are_not_caught(self):
        """Token discipline: only the WHOLE token ``plane`` matches —
        ``plan`` / ``planning`` / ``plan-ledger`` are the desired vocabulary
        and must never trip the gate."""
        texts = self._clean()
        texts["agents/ari/soul.md"] += (
            "\nThe plan is the ledger; planning stays board-light.\n"
        )
        assert _whole_file_violations(texts) == []
