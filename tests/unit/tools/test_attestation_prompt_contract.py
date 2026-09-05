"""Prompt-contract greps for the leader completion attestation feature
(Phase 1, 2026-09-05).

The LCA contract instructs the leader LLM to:

1. Call ``attest_completion`` BEFORE declaring itself done (not in
   plain text).
2. Treat the in-graph continuation nudge ("The work is not yet
   finished — check current progress and continue.") as a real user
   instruction.

Both pieces of prose live in TWO files (the contract has TWO
canonical homes per the prompt-writing-guide one-canonical-home
convention adjusted for prompt mirroring — see ``docs/agent-prompt-writing-guide.md``):

* ``agents/leader/rule.md`` — canonical home under ``## Must`` as a
  ``### Must`` block.
* ``agents/leader/workflow.md`` — mirror block at the workflow
  stage so the leader sees the contract in both prompt contexts.

These tests pin (a) that both files contain the contract text, and
(b) that the rule.md block sits under a ``## Must`` heading. Drift
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
    # Continuation nudge verbatim — Phase 2 gate writes this text;
    # the leader is expected to recognize and act on it.
    "The work is not yet finished — check current progress and continue",
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


# ── workflow.md: prompt-context mirror ───────────────────────────────────────


class TestWorkflowMdMirror:
    """The contract MUST be mirrored in ``agents/leader/workflow.md``
    so the leader sees it during dispatch-time instructions (the
    rule.md block is rules-time; the workflow.md block is
    workflow-time — both prompt contexts must carry the contract)."""

    @pytest.fixture
    def source(self) -> str:
        return WORKFLOW_MD.read_text(encoding="utf-8")

    def test_file_exists(self) -> None:
        assert WORKFLOW_MD.exists(), f"missing workflow.md at {WORKFLOW_MD}"

    @pytest.mark.parametrize("fragment", CONTRACT_FRAGMENTS)
    def test_contract_fragment_present(self, source: str, fragment: str) -> None:
        assert fragment in source, (
            f"workflow.md is missing contract fragment: {fragment!r}. "
            f"The mirror is required so the contract appears in both "
            f"prompt contexts (rules + workflow)."
        )

    def test_mirror_callout_present(self, source: str) -> None:
        """The mirror MUST explicitly note that it is a mirror of
        the canonical-home contract in rule.md — so a maintainer
        editing one side knows to edit the other. One-canonical-home
        convention forbids divergent copies; the callout is the
        operational enforcement mechanism."""
        assert "mirror" in source.lower() and (
            "rule.md" in source or "rule.md" in source
        ), (
            "workflow.md must explicitly note the mirror relationship "
            "to rule.md so edits stay in sync"
        )