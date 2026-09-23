"""RETIRED 2026-09-23 — Incident b2f4dae9: LCA (b)-path Completion Check Note full removal.

The LCA (b)/(d)-with-pending route still resolves to allow on the resolver
row (label ``allow_hint``), but NO message is injected, NO
``context_kind=task_context`` block is minted, NO stable id is required
for LangGraph upsert. The four tests in this file's pre-retirement form
(``test_two_consecutive_b_events_supersede_to_one_block_real_add_messages``,
``test_b_supersede_via_real_checkpoint_two_turns_one_block``,
``test_b_supersede_different_instances_yield_two_blocks_real_add_messages``,
``test_stable_id_format_matches_w2_shape_a_contract``) pinned the W2
Shape A contract on ``_make_completion_check_note_message`` — that
factory is RETIRED end-to-end along with the entire hint surface.

The single remaining test (``test_lca_note_supersede_class_retired``)
is the WITNESS that the contract was retired: it asserts the factory,
constant, and stable-id table row are all gone. The live forensic-
surface tests for the (b)/(d)-with-pending log-only route live in
``tests/unit/test_attestation_lca_note_removed.py`` (new file added
2026-09-23) — the b2f4dae9 regression pin + the suspect-pending
shapes + the negative census pin.

The retirement preserves the file path (so any external tooling that
greps for ``test_attestation_marker_supersede_lca.py`` keeps working)
and the langgraph mock-swap / restore fixture (now vestigial but kept
to avoid breaking the conftest integration contract — if you find
yourself re-introducing the supersede contract, restore the original
test bodies from git history at ``feature/lca-remove-check-note``).

The original file body (4 tests, 352 lines) is REPLACED below. See
``.agents/shared/planning/leader-completion-attestation/decisions.md``
D-entry 2026-09-23 for the rationale + the age-gate fallback design
that was rejected in favor of full removal.
"""

from __future__ import annotations

import pytest


def test_lca_note_supersede_class_retired():
    """2026-09-23 (b2f4dae9): LCA (b)-path Completion Check Note RETIRED.

    The four supersede tests in this file's pre-retirement form
    (``test_two_consecutive_b_events_supersede_to_one_block_real_add_messages``,
    ``test_b_supersede_via_real_checkpoint_two_turns_one_block``,
    ``test_b_supersede_different_instances_yield_two_blocks_real_add_messages``,
    ``test_stable_id_format_matches_w2_shape_a_contract``) pinned the
    W2 Shape A contract on the ``_make_completion_check_note_message``
    factory. The factory + the constant + the canonical
    ``_stable_id_for`` table row are RETIRED end-to-end; the
    (b)/(d)-with-pending route resolves to allow log-only on the
    resolver row (no message, no context_kind, no stable-id). This
    test is the WITNESS that the contract was retired — if it ever
    fails, the supersede contract was re-introduced without
    re-anchoring the test surface.
    """
    import daemon.graph
    import daemon.services.context_messages as ctx_messages

    # The hint factory is gone.
    assert not hasattr(daemon.graph, "_make_completion_check_note_message"), (
        "the W2 Shape A factory MUST stay retired (incident b2f4dae9); "
        "if this assertion fails, the factory was re-introduced without "
        "re-anchoring the contract — restore the original test bodies "
        "from git history at feature/lca-remove-check-note and pin the "
        "new log-only contract in test_attestation_lca_note_removed.py"
    )
    # The text constant is gone.
    assert not hasattr(daemon.graph, "COMPLETION_CHECK_NOTE_TEXT"), (
        "COMPLETION_CHECK_NOTE_TEXT MUST stay retired (incident "
        "b2f4dae9); the canonical hint text lives nowhere in production"
    )
    # The stable-id table row is gone — the kind is rejected.
    with pytest.raises(ValueError) as exc_info:
        ctx_messages._stable_id_for("completion_check_note", instance_id="any-iid")
    assert "unknown kind" in str(exc_info.value) and (
        "completion_check_note" not in str(exc_info.value)
    ) or (
        # Defensive: if the kind literal survived in the message,
        # the table-row check still must reject it.
        "completion_check_note" in str(exc_info.value)
    ), (
        f"_stable_id_for MUST reject the retired kind; got error: "
        f"{exc_info.value}"
    )
