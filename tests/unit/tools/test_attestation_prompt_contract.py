"""Prompt-contract greps for the leader completion attestation feature
(2026-09-06 — conditional gate amendment).

The LCA contract instructs the leader LLM to:

1. Call ``attest_completion`` ONLY when the mission **delegated** —
   the conditional gate (Phase 6 fastfollow, FR-3 conditionality)
   fires when a ``send_message`` tool call happened since the last
   real user message. Plain questions, chart requests, and other
   non-delegating turns complete normally without the gate firing.
2. Treat the in-graph continuation nudge (a user message whose body
   begins with ``[SYSTEM CONTEXT: Completion Check Nudge]``) as a
   real user instruction. The nudge carries a leading system-context
   header line so the LLM recognizes it as system-origin (the role
   is still user-authored; the header is the marker).

The contract has ONE canonical home:

* ``agents/leader/rule.md`` — the full contract prose under ``## Must``
  as a ``### Must`` block.
* ``agents/leader/workflow.md`` — a ONE-LINE POINTER to the rule.md
  contract (no verbatim restatement). The former full mirror was
  collapsed in the 2026-09-05 LCA post-approval quality pass: the copy
  had already drifted (it omitted the Source-of-message note while
  duplicating the rest), so the one-canonical-home convention now
  applies without the mirror exception. The pointer MUST name the
  tool and reference rule.md; it must not restate the contract.

These tests pin (a) that rule.md contains the conditional contract
text under a ``## Must`` heading, (b) that the unconditional MUST-
call language is GONE (Phase 6 amendment — the nudge text is now the
instruction source), and (c) that workflow.md's pointer names the
tool and the canonical home WITHOUT duplicating the contract prose.
Drift here is silent — the contract disappears from the leader's
prompt without any test failure unless the contract text is pinned.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# Repo root: tests/unit/tools/test_attestation_prompt_contract.py -> parents[3]
REPO_ROOT = Path(__file__).resolve().parents[3]
RULE_MD = REPO_ROOT / "agents" / "leader" / "rule.md"
WORKFLOW_MD = REPO_ROOT / "agents" / "leader" / "workflow.md"

# Pin: the unconditional MUST-call contract is GONE in rule.md
# (Phase 6 fastfollow 2026-09-06, FR-3 conditionality). The prompts no
# longer teach the unconditional MUST; the runtime nudge
# (``ATTESTATION_NUDGE_TEXT`` in ``daemon/graph.py``) is now the
# instruction source for delegated missions. If this tuple ever
# goes empty in rule.md, that means the conditional semantics have
# been silently retracted — a hard regression because the leader
# would not know what to do when delegated.
UNCONDITIONAL_MUST_CONTRACT_FRAGMENTS = (
    # 2026-09-06 retracted — unconditional MUST-call for every turn
    # ("Before declaring yourself done, you MUST call the
    # `attest_completion` tool"). Pinning absence here guards the
    # amendment.
    "Before declaring yourself done, you MUST call the `attest_completion` tool",
)

# Substring fragments the CONDITIONAL contract MUST contain verbatim.
# Each is the byte-stable language the leader LLM is expected to see in
# its prompt for DELEGATED missions; pinning is brittle-but-deliberate
# (the contract text is the documented gate input for Phase 2's
# scanner + nudge logic, and silent drift would break the leader's
# behavior with no test signal otherwise).
CONTRACT_FRAGMENTS = (
    # Conditional semantics (2026-09-06) — the gate is conditional on
    # delegation. The unconditional MUST is replaced with a gate ON
    # ONLY when this mission delegated.
    "CONDITIONAL on delegation",
    "this mission dispatched a child via `send_message`",
    # Per-delegated-mission MUST contract — preserved from Phase 1.
    "Deliver the full detailed final report",
    "as its own message FIRST",
    "call `attest_completion` ALONE",
    "Compress the report into the `attest_completion` call",
    # The system-context nudge header the leader must recognize as a
    # real user instruction (the runtime contract source for delegated
    # missions).
    "[SYSTEM CONTEXT: Completion Check Nudge]",
    "treat it as a real user instruction",
)

# Anchor fragment for the heading / sub-block structural tests.
# Searches for the conditional gate explanation as the structural
# landmark because the unconditional MUST is gone.
CONTRACT_HEADING_ANCHOR = "CONDITIONAL on delegation"


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

    @pytest.mark.parametrize("fragment", UNCONDITIONAL_MUST_CONTRACT_FRAGMENTS)
    def test_unconditional_must_contract_removed(
        self, source: str, fragment: str
    ) -> None:
        """The unconditional MUST-call contract MUST be absent from
        rule.md (FR-3 conditionality, 2026-09-06). This is the
        amendment guard: if the unconditional language creeps back
        in, the leader would over-call ``attest_completion`` on
        non-delegated turns (the exact bug the amendment fixed)."""
        assert fragment not in source, (
            f"rule.md still carries the retracted unconditional MUST-call "
            f"contract: {fragment!r}. FR-3 conditionality requires the "
            f"gate to be off for non-delegating turns. Restore the "
            f"conditional framing."
        )

    def test_contract_sits_under_must_heading(self, source: str) -> None:
        """The contract block MUST sit under a ``## Must`` heading
        (per ``agents/leader/rule.md`` house style). A drift to
        ``## Workflow`` or ``## Should`` weakens the rule's authority
        and the leader LLM may treat it as advisory."""
        must_idx = source.find("## Must")
        assert must_idx != -1, "rule.md has no ## Must heading"
        # The contract must appear AFTER the ## Must heading
        contract_idx = source.find(CONTRACT_HEADING_ANCHOR)
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
        contract_idx = source.find(CONTRACT_HEADING_ANCHOR)
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

    def test_pointer_names_conditional_semantics(self, source: str) -> None:
        """The pointer MUST identify the conditional semantics
        (FR-3, 2026-09-06) — a workflow-context reader has to know
        the gate fires only on delegated missions or they will
        over-call ``attest_completion``."""
        assert "CONDITIONAL" in source, (
            "workflow.md pointer must name the conditional semantics "
            "(CONDITIONAL on delegation / phase 6 amendment)"
        )

    def test_no_verbatim_contract_duplication(self, source: str) -> None:
        """The contract prose must live ONLY in rule.md — workflow.md
        must not restate the nudge text or the MUST/MAY language."""
        assert "The work is not yet finished" not in source, (
            "workflow.md duplicates the canonical nudge prose — collapse "
            "to the one-line pointer (one-canonical-home convention)"
        )
        assert "treat it as a real user instruction" not in source, (
            "workflow.md duplicates the canonical nudge rule — collapse "
            "to the one-line pointer"
        )