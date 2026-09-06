"""Prompt-contract greps for the leader completion attestation feature
(Phase 1, 2026-09-05).

The LCA contract instructs the leader LLM to:

1. Call ``attest_completion`` BEFORE declaring itself done (not in
   plain text).
2. Treat the in-graph continuation nudge ("The work is not yet
   finished — check current progress (tasks/children status) and
   continue.") as a real user instruction.

The contract has ONE canonical home:

* ``agents/leader/rule.md`` — the full contract prose under ``## Must``
  as a ``### Must`` block.
* ``agents/leader/workflow.md`` — a ONE-LINE POINTER to the rule.md
  contract (no verbatim restatement). The former full mirror was
  collapsed in the 2026-09-05 LCA post-approval quality pass: the copy
  had already drifted (it omitted the Source-of-message note while
  duplicating the rest), so the one-canonical-home convention now
  applies without the mirror exception.

These tests pin (a) that rule.md contains the contract text under a
``## Must`` heading, and (b) that workflow.md's pointer names the tool
and the canonical home WITHOUT duplicating the contract prose. Drift
here is silent — the contract disappears from the leader's prompt
without any test failure unless the contract text is pinned.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# Repo root: tests/unit/tools/test_attestation_prompt_contract.py -> parents[3]
REPO_ROOT = Path(__file__).resolve().parents[3]
RULE_MD = REPO_ROOT / "agents" / "leader" / "rule.md"
WORKFLOW_MD = REPO_ROOT / "agents" / "leader" / "workflow.md"

# Substring fragments the contract MUST contain verbatim. Each is the
# byte-stable language the leader LLM is expected to see in its
# prompt; pinning is brittle-but-deliberate (the contract text is the
# documented gate input for Phase 2's scanner + nudge logic, and
# silent drift would break the leader's behavior with no test
# signal otherwise).
CONTRACT_FRAGMENTS = (
    # Core action — call the tool before declaring done.
    "you MUST call the `attest_completion` tool",
    "Do not declare done in plain text",
    # Two-step contract (2026-09-06) — detailed report first, tool
    # call alone; the report is never compressed into the
    # attestation step.
    "as its own message FIRST",
    "call `attest_completion` ALONE",
    "Compress the report into the `attest_completion` call",
    # Continuation nudge verbatim — Phase 2 gate writes this text;
    # the leader is expected to recognize and act on it.
    "The work is not yet finished — check current progress (tasks/children status) and continue",
    "treat it as a real user instruction",
)


# ── rule.md: canonical home under ## Must ────────────────────────────────────


class TestRuleMdContract:
    """The contract's canonical home is ``agents/leader/rule.md`` —
    a new ``### Must`` block under ``## Must`` (per the project's
    house style for mandatory leader rules). The contract text is
    the byte-stable gate input; drift here is silent."""

    @pytest.fixture
    def source(self) -> str:
        return RULE_MD.read_text(encoding="utf-8")

    def test_file_exists(self) -> None:
        assert RULE_MD.exists(), f"missing rule.md at {RULE_MD}"

    @pytest.mark.parametrize("fragment", CONTRACT_FRAGMENTS)
    def test_contract_fragment_present(self, source: str, fragment: str) -> None:
        assert fragment in source, (
            f"rule.md is missing contract fragment: {fragment!r}. "
            f"The leader LLM will not see the contract and may "
            f"declare done in plain text — silent regression."
        )

    def test_contract_sits_under_must_heading(self, source: str) -> None:
        """The contract block MUST sit under a ``## Must`` heading
        (per ``agents/leader/rule.md`` house style). A drift to
        ``## Workflow`` or ``## Should`` weakens the rule's authority
        and the leader LLM may treat it as advisory."""
        must_idx = source.find("## Must")
        assert must_idx != -1, "rule.md has no ## Must heading"
        # The contract must appear AFTER the ## Must heading
        contract_idx = source.find("you MUST call the `attest_completion` tool")
        assert contract_idx > must_idx, (
            f"contract must appear under ## Must heading (must@{must_idx}, "
            f"contract@{contract_idx})"
        )

    def test_contract_uses_must_subblock_syntax(self, source: str) -> None:
        """The contract is structured as a ``### Must`` sub-block
        (or equivalent — but it MUST be a third-level heading, not
        bare prose), matching the existing rule.md house style
        (e.g. ``### 🚨 NO REAL WORK — BRAIN ONLY``)."""
        # Look for a ### heading close to the contract text
        contract_idx = source.find("you MUST call the `attest_completion` tool")
        assert contract_idx != -1
        # Walk backwards from the contract to find the nearest ###
        prefix = source[:contract_idx]
        last_h3 = prefix.rfind("\n### ")
        assert last_h3 != -1, (
            "contract must live under a ### Must sub-block, not bare prose"
        )
        # The ### heading must appear AFTER ## Must (no nested ## higher up)
        last_h2_in_prefix = prefix.rfind("\n## ")
        assert last_h2_in_prefix < last_h3, (
            f"### heading @{last_h3} must come after the most recent ## "
            f"heading @{last_h2_in_prefix} — a higher-level heading before "
            f"the ### block would put the contract outside the rule's scope"
        )


# ── workflow.md: one-line pointer to the canonical home ──────────────────────


class TestWorkflowMdPointer:
    """``agents/leader/workflow.md`` carries a ONE-LINE POINTER to the
    canonical rule.md contract — it must name the tool and the canonical
    home, and must NOT duplicate the contract prose (the former verbatim
    mirror drifted: it omitted the Source-of-message note while copying
    the rest)."""

    @pytest.fixture
    def source(self) -> str:
        return WORKFLOW_MD.read_text(encoding="utf-8")

    def test_file_exists(self) -> None:
        assert WORKFLOW_MD.exists(), f"missing workflow.md at {WORKFLOW_MD}"

    def test_pointer_names_tool_and_canonical_home(
        self, source: str
    ) -> None:
        """The pointer must name the tool AND point at rule.md —
        otherwise a workflow-context reader has no route to the
        contract."""
        assert "attest_completion" in source, (
            "workflow.md pointer must name the attest_completion tool"
        )
        assert "rule.md" in source, (
            "workflow.md pointer must name the canonical home (rule.md)"
        )

    def test_no_verbatim_contract_duplication(self, source: str) -> None:
        """The contract prose must live ONLY in rule.md — workflow.md
        must not restate the nudge text or the MUST/MAY language."""
        assert "The work is not yet finished" not in source, (
            "workflow.md duplicates the canonical nudge prose — collapse "
            "to the one-line pointer (one-canonical-home convention)"
        )
        assert "Do not declare done in plain text" not in source, (
            "workflow.md duplicates the canonical contract sentence — "
            "collapse to the one-line pointer"
        )
        assert "treat it as a real user instruction" not in source, (
            "workflow.md duplicates the canonical nudge rule — collapse "
            "to the one-line pointer"
        )