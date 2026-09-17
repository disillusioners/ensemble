"""LCA Stage-3 ledger item (a) — B-section leader-prose redaction pin.

Closes the discovery gap: ``TestFusedBundle`` asserts SOURCE A/B/C
presence + A/C redaction but NOT B-section redaction with UUID ids
the leader quoted in its own prose (98b59dd7 boundary forbids raw
instance ids in the bundle that reaches the judge).

Seam: ``daemon/services/attestation_resolver_activation.py:750``
  → ``daemon/graph.py:5311`` ``judge_fused_bundle_async(act.bundle.text, …)``.
"""
from __future__ import annotations

import uuid as _uuid
from langchain_core.messages import AIMessage
from daemon.services import attestation_resolver_activation as ara
from daemon.services.attestation_resolver_activation import (
    SourceASignals, SourceBSignals, SourceCSignals, assemble_fused_bundle,
)

UUID_A, UUID_B, UUID_C = (
    "11111111-aaaa-bbbb-cccc-222222222222",
    "33333333-dddd-eeee-ffff-444444444444",
    "55555555-1111-2222-3333-666666666666",
)
NOT_AN_ID = "agent-7-final"  # Negative control — hyphenated but NOT 8-4-4-4-12 hex.
LEADER_PROSE = (
    f"Mid-work report: worker {UUID_A} finished cleanly. "
    f"Worker {UUID_B} is past the lint/format stage. "
    f"Worker {UUID_C} has been silent for 18 minutes and needs a nudge. "
    f"Decision pivot: {NOT_AN_ID} is the active reviewer. "
    "No mid-work children are blocked; no user_answer_pending."
)
_DEFAULT_NOTE = (ara.ChildReportCheckEvidence(
    child_instance_id=str(_uuid.uuid4()), matched_terms=("ending turn",),
    note_excerpt="x", stable_id="child_report_check:abc", kwargs_surface_seen=True,
),)


def _bundle(ai_tail=None, a_notes=_DEFAULT_NOTE):
    return assemble_fused_bundle(
        a_signals=SourceASignals(True, True, False, False, 1, a_notes),
        b_signals=SourceBSignals(False, (), False, len(LEADER_PROSE.split()), False),
        c_signals=SourceCSignals(3, 1, 3, 1, False),
        c_tree_rows=[],
        ai_tail_messages=ai_tail or [AIMessage(content=LEADER_PROSE)],
    )


class TestBSectionRedactionPin:
    """Stage-3 ledger item (a): B-section leader-prose redaction."""

    def test_b_section_redacts_quoted_uuids_and_preserves_prose(self):
        # Positive control — input MUST carry raw UUIDs + non-id token
        # (else a regression dropping redact_ids from _build_b_section
        # silently greens — vacuous fixture).
        for tok in (UUID_A, UUID_B, UUID_C, NOT_AN_ID):
            assert tok in LEADER_PROSE, f"vacuous: {tok!r} not in fixture"
        text = _bundle().text
        for u in (UUID_A, UUID_B, UUID_C):
            assert u not in text, f"B-section leaked raw UUID {u}"
        for n in (1, 2, 3):
            assert f"<redacted-leader-{n}>" in text, f"missing placeholder leader-{n}"
        for prose in ("Mid-work report: worker", "finished cleanly.",
                      "has been silent for 18 minutes and needs a nudge.",
                      "No mid-work children are blocked; no user_answer_pending."):
            assert prose in text, f"non-UUID prose {prose!r} was corrupted"

    def test_bundle_structure_a_b_c_intact(self):
        text = _bundle().text
        assert text.startswith("[LCA FUSED EVIDENCE BUNDLE v1]")
        for header in ("=== SOURCE A:", "=== SOURCE B:", "=== SOURCE C:"):
            assert header in text

    def test_a_section_regression_guard_in_same_bundle(self):
        """Same bundle must STILL redact A-section child ids (regression)."""
        u = "99999999-8888-7777-6666-555555555555"
        a_note = ara.ChildReportCheckEvidence(
            child_instance_id=u, matched_terms=("ending turn",),
            note_excerpt=f"Child {u} completed while its final report promises future work.",
            stable_id="child_report_check:abc", kwargs_surface_seen=True,
        )
        text = _bundle(a_notes=(a_note,)).text
        assert u not in text, "A-section regression: child uuid leaked"
        assert "<redacted-child-1>" in text
        # B-section redaction still holds in the same bundle.
        assert UUID_A not in text and "<redacted-leader-1>" in text

    def test_non_uuid_idshaped_token_survives_verbatim(self):
        """Negative control: redact_ids MUST NOT match non-UUID tokens."""
        text = _bundle().text
        assert NOT_AN_ID in text, (
            f"redact_ids over-matched: {NOT_AN_ID!r} was redacted. "
            f"_UUID_TOKEN_RE is 8-4-4-4-12 ALL-HEX with word boundaries."
        )