"""Prompt-contract greps for the leader completion attestation feature
(2026-09-06 — conditional gate; 2026-09-08 — prompt-contract section
retracted, suppression rule added, tool description condensed).

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

These tests pin (a) the ABSENCE side of the standing prompt-contract
section after the 2026-09-08 retraction: in BOTH
``agents/leader/rule.md`` and ``agents/leader/workflow.md`` we assert
that the LCA section headings, the conditional-contract fragments,
and the old unconditional-contract fragments are all gone. The sole
survivor in ``agents/leader/`` is the ``attestation`` entry in
``meta.json`` ``tools.allow`` (tool-inventory mention — KEEP).

These tests ALSO pin the PRESENCE side of the 2026-09-08 follow-up
amendment: rule.md MUST carry a small suppression rule ("do not
call ``attest_completion`` unless the system nudges you"), and
``daemon/tools/attestation.py`` MUST carry the concise conditional
tool description (CONDITIONAL framing + when-not-to-call + ack-note
fragments). Drift here is silent: the suppression rule could silently
vanish, or the tool description could bloat back to the old verbose
shape, with no test failure unless the rules are pinned.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# Repo root: tests/unit/tools/test_attestation_prompt_contract.py -> parents[3]
REPO_ROOT = Path(__file__).resolve().parents[3]
RULE_MD = REPO_ROOT / "agents" / "leader" / "rule.md"
WORKFLOW_MD = REPO_ROOT / "agents" / "leader" / "workflow.md"
ATTESTATION_PY = REPO_ROOT / "daemon" / "tools" / "attestation.py"

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

# Suppression-rule fragments (2026-09-08 follow-up amendment): the
# leader prompt MUST carry a small suppression rule that prevents the
# leader from calling ``attest_completion`` on its own initiative.
# LLMs see the tool in their toolset and tend to call it
# spontaneously even on non-delegated missions — the suppression rule
# is the prompt-side guard, paired with the concise conditional tool
# description (DOCSTRING_FRAGMENTS below) which communicates the
# conditional framing to the LLM at tool-listing time. Pinning
# PRESENCE here guards against the rule silently vanishing.
SUPPRESSION_RULE_FRAGMENTS = (
    # The rule's heading (a `### ❌` subsection under `## Must Not`).
    "Spontaneous `attest_completion`",
    # The negative instruction — DO NOT call unless the system nudges.
    "DO NOT call `attest_completion` unless the system nudges you",
    # The non-delegation carve-out — quick answers, charts, follow-ups,
    # and any mission that did not dispatch children never need it.
    "did not delegate via `send_message`",
    # The "just complete normally" tail (closes the suppression).
    "just complete normally",
)

# Concise-conditional docstring fragments (2026-09-08 follow-up
# amendment): ``daemon/tools/attestation.py`` MUST carry the concise
# conditional tool description — CONDITIONAL framing, when-not-to-call
# guidance, and the ack-note example. The docstring is shown to the
# LLM at tool-listing time and is the second half of the suppression
# defense (the suppression rule is the first half). Pinning PRESENCE
# here guards against the description silently bloating back to the
# old verbose shape (which conditioned the LLM to call the tool on
# every turn) or losing its conditional framing.
DOCSTRING_FRAGMENTS = (
    # Conditional framing — must lead with this so the LLM knows the
    # tool is conditional on its' situation.
    "CONDITIONAL",
    # When-not-to-call — explicit carve-out for plain answers, charts,
    # quick follow-ups, non-delegating missions.
    "DO NOT call for plain answers",
    # When the gate or judge has already released — explicit ack that
    # the nudge is the trigger, not self-motivation.
    "the gate or judge has already released",
    # Ack-note example — verbatim, so the LLM sees a concrete phrasing.
    "Report delivered above; attesting completion.",
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

    @pytest.mark.parametrize("fragment", SUPPRESSION_RULE_FRAGMENTS)
    def test_suppression_rule_present(self, source: str, fragment: str) -> None:
        """The 2026-09-08 suppression rule MUST be present in rule.md.
        LLMs see ``attest_completion`` in their toolset and tend to
        call it spontaneously even on non-delegated missions; the
        rule is the prompt-side guard. Removing it would silently
        let spontaneous over-calls return."""
        assert fragment in source, (
            f"rule.md is missing the suppression-rule fragment: "
            f"{fragment!r}. The 2026-09-08 amendment added a small "
            f"rule that prevents the leader from calling "
            f"``attest_completion`` unless the system nudges it. "
            f"Restore the rule."
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


# ── attestation.py: concise conditional tool description (2026-09-08) ───────


class TestAttestationToolDocstring:
    """``daemon/tools/attestation.py`` carries the tool description
    the LLM sees at tool-listing time. The 2026-09-08 amendment
    condensed this description to be CONCISE and CONDITIONAL —
    leading with the conditional framing, an explicit when-not-to-call
    block, and the ack-note example — so the LLM does not infer an
    unconditional MUST-call obligation from the description alone.

    These tests pin PRESENCE so a future contributor cannot silently
    bloat the description back to the old verbose shape (which
    conditioned the LLM to call the tool on every turn) or strip the
    conditional framing. The module header docstring + the tool's own
    docstring + ``_full_doc_`` are all part of the same source file,
    so a single parametrized presence check covers all three surfaces.
    """

    @pytest.fixture
    def source(self) -> str:
        return ATTESTATION_PY.read_text(encoding="utf-8")

    def test_file_exists(self) -> None:
        assert ATTESTATION_PY.exists(), (
            f"missing attestation.py at {ATTESTATION_PY}"
        )

    @pytest.mark.parametrize("fragment", DOCSTRING_FRAGMENTS)
    def test_docstring_fragment_present(self, source: str, fragment: str) -> None:
        """Each fragment of the concise conditional tool description
        MUST be present somewhere in ``daemon/tools/attestation.py``.
        The fragments collectively pin: the CONDITIONAL framing, the
        when-not-to-call guidance, the nudge-as-trigger framing, and
        the ack-note example."""
        assert fragment in source, (
            f"attestation.py is missing the concise-conditional "
            f"docstring fragment: {fragment!r}. The 2026-09-08 "
            f"amendment condensed the tool description to communicate "
            f"the conditional framing at tool-listing time; restoring "
            f"the verbose shape or stripping the conditional framing "
            f"would let LLMs over-call the tool on non-delegated turns."
        )