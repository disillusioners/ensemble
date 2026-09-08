"""Prompt-contract greps for the leader completion attestation feature
(2026-09-06 — conditional gate; 2026-09-08 — prompt-contract section
retracted).

The LCA feature ships in three runtime layers:

1. ``daemon/tools/attestation.py`` — the ``attest_completion`` tool
   itself (idempotent, no-op body, returns a confirmation frame).
2. ``daemon/graph.py`` — the in-graph completion gate that scans the
   most recent ``N`` AIMessages for an ``attest_completion`` tool_call
   to decide whether to allow or deny the END transition. The gate is
   OFF for missions that did NOT delegate (no ``send_message`` tool
   call since the last real user message) — FR-3 conditionality.
3. The deny-time continuation nudge (``ATTESTATION_NUDGE_TEXT``) — a
   user-authored message whose body begins with
   ``[SYSTEM CONTEXT: Completion Check Nudge]``. The nudge carries
   the conditional semantics, the two-step contract, and the embedded
   mermaid diagram; it is the SOLE teaching source on the prompt side
   (2026-09-08 decision: a standing prompt-contract section is
   redundant and was removed).

These tests pin the ABSENCE side of the prompt-contract after the
2026-09-08 retraction. Specifically: in BOTH ``agents/leader/rule.md``
and ``agents/leader/workflow.md`` we assert that (a) the LCA section
headings are gone, (b) the conditional-contract fragments are gone,
and (c) the old unconditional-contract fragments remain gone. The
sole survivor in ``agents/leader/`` is the ``attestation`` entry in
``meta.json`` ``tools.allow`` (tool-inventory mention — KEEP). Drift
here is silent: the contract disappearing from the leader's prompt
without any test failure would mean a future contributor silently
re-introduces a redundant or worse stale prompt section.
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
# appears in rule.md again, that means the unconditional semantics
# have been silently re-introduced — a hard regression because the
# leader would over-call ``attest_completion`` on non-delegating
# turns.
UNCONDITIONAL_MUST_CONTRACT_FRAGMENTS = (
    # 2026-09-06 retracted — unconditional MUST-call for every turn
    # ("Before declaring yourself done, you MUST call the
    # `attest_completion` tool"). Pinning absence here guards the
    # amendment.
    "Before declaring yourself done, you MUST call the `attest_completion` tool",
)

# Substring fragments the CONDITIONAL contract used to carry verbatim
# in the leader prompt (Phase 1 contract, collapsed in the 2026-09-08
# retraction). Each fragment was a byte-stable part of the leader's
# prompt for DELEGATED missions; pinning ABSENCE here guards against
# a future contributor silently re-introducing the redundant prompt
# section. The deny-time nudge is now the sole teaching source.
CONDITIONAL_CONTRACT_FRAGMENTS = (
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

# Section-heading fragments that MUST be absent from the leader
# prompt after the 2026-09-08 retraction. The rule.md heading was
# level-3 ("###") and the workflow.md heading was level-2 ("##"); both
# named the LCA feature explicitly.
LCA_HEADING_FRAGMENTS = (
    "### 📜 Completion Attestation (LCA feature — conditional, 2026-09-06)",
    "## Completion Attestation (LCA feature — conditional, 2026-09-06)",
)


# ── rule.md: prompt-contract section retracted (2026-09-08) ──────────────


class TestRuleMdContract:
    """The LCA prompt-contract section was removed from
    ``agents/leader/rule.md`` on 2026-09-08. The deny-time nudge is
    the sole teaching source. These tests pin ABSENCE so a future
    contributor cannot silently re-introduce the redundant section
    (or a stale variant of it)."""

    @pytest.fixture
    def source(self) -> str:
        return RULE_MD.read_text(encoding="utf-8")

    def test_file_exists(self) -> None:
        assert RULE_MD.exists(), f"missing rule.md at {RULE_MD}"

    @pytest.mark.parametrize("fragment", LCA_HEADING_FRAGMENTS)
    def test_lca_heading_absent(self, source: str, fragment: str) -> None:
        """The LCA section heading(s) MUST be absent from rule.md.
        Re-introducing either heading would silently put the contract
        back in the leader's prompt — bypassing the deny-time nudge
        as the sole teaching source."""
        assert fragment not in source, (
            f"rule.md still carries the retracted LCA heading: "
            f"{fragment!r}. The 2026-09-08 decision removed this "
            f"section from the leader prompt; the deny-time nudge is "
            f"the sole teaching source."
        )

    @pytest.mark.parametrize("fragment", CONDITIONAL_CONTRACT_FRAGMENTS)
    def test_contract_fragment_absent(self, source: str, fragment: str) -> None:
        """The conditional-contract fragments MUST be absent from
        rule.md. The deny-time nudge carries the conditional
        semantics, the two-step contract, and the embedded mermaid
        in-graph; a static prompt-section restatement is redundant
        and risks drift from the runtime contract source."""
        assert fragment not in source, (
            f"rule.md still carries the retracted conditional-contract "
            f"fragment: {fragment!r}. The 2026-09-08 decision retired "
            f"the static prompt section; the deny-time nudge is the "
            f"sole teaching source. Remove the static mention."
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


# ── workflow.md: pointer collapsed to sole teaching source (2026-09-08) ─────


class TestWorkflowMdPointer:
    """``agents/leader/workflow.md`` previously carried a one-line
    pointer to the rule.md contract. On 2026-09-08 that pointer was
    removed alongside the rule.md section: the deny-time nudge is
    the sole teaching source, and a static pointer to a non-existent
    canonical home is stale by definition. These tests pin ABSENCE
    so a future contributor cannot silently re-introduce the pointer
    (or a duplicate contract block)."""

    @pytest.fixture
    def source(self) -> str:
        return WORKFLOW_MD.read_text(encoding="utf-8")

    def test_file_exists(self) -> None:
        assert WORKFLOW_MD.exists(), f"missing workflow.md at {WORKFLOW_MD}"

    @pytest.mark.parametrize("fragment", LCA_HEADING_FRAGMENTS)
    def test_lca_heading_absent(self, source: str, fragment: str) -> None:
        """The LCA section heading(s) MUST be absent from workflow.md.
        Re-introducing the heading would silently put the (now
        redundant) contract back in the leader's prompt."""
        assert fragment not in source, (
            f"workflow.md still carries the retracted LCA heading: "
            f"{fragment!r}. The 2026-09-08 decision removed this "
            f"pointer; the deny-time nudge is the sole teaching source."
        )

    @pytest.mark.parametrize("fragment", CONDITIONAL_CONTRACT_FRAGMENTS)
    def test_contract_fragment_absent(self, source: str, fragment: str) -> None:
        """The conditional-contract fragments MUST be absent from
        workflow.md (the former pointer used the conditional
        semantics language verbatim)."""
        assert fragment not in source, (
            f"workflow.md still carries the retracted conditional-contract "
            f"fragment: {fragment!r}. The 2026-09-08 decision retired "
            f"the static pointer; the deny-time nudge is the sole "
            f"teaching source. Remove the static mention."
        )

    def test_no_verbatim_contract_duplication(self, source: str) -> None:
        """The contract prose must live ONLY in the runtime nudge
        (and the tool docstring). workflow.md must not restate the
        nudge text or the MUST/MAY language."""
        assert "The work is not yet finished" not in source, (
            "workflow.md duplicates the canonical nudge prose — collapse "
            "to the one-line pointer (one-canonical-home convention)"
        )
        assert "treat it as a real user instruction" not in source, (
            "workflow.md duplicates the canonical nudge rule — collapse "
            "to the one-line pointer"
        )