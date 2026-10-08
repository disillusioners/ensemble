"""G5 gate pin — heal idempotence.

Healing must converge: the SECOND ``validate_and_heal_messages``
pass over an already-healed history is a strict no-op (identical
length and contents), and placeholder artifacts already present in
a history (``partner-synth-`` / ``pairing-synth-`` ids) are
recognized and never re-flagged — the re-heal across the helper
chain (in-graph guard + this module) stays idempotent.

COVERAGE NOTE: tests/unit/tool_pairing_history/test_full_history_heal.py
pins the single-heal convergence deeply; a dedicated second-pass
idempotence pin was not present there — this gate file adds it as
a thin boundary smoke.
"""

from __future__ import annotations

import copy

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from daemon.tool_pairing_history import (
    PARTNER_SYNTH_TEXT,
    has_pairing_violations,
    validate_and_heal_messages,
)


def _tc(tc_id: str, name: str = "tool") -> dict:
    """Minimal tool-call dict in langchain_core contract shape."""
    return {
        "id": tc_id,
        "name": name,
        "args": {"x": 1},
        "type": "tool_call",
    }


class TestHealIdempotence:
    def test_second_heal_is_noop(self):
        msgs: list = [
            SystemMessage(content="sys"),
            AIMessage(content="", tool_calls=[_tc("call_x")]),
            SystemMessage(content="sys-intervening"),
            HumanMessage(content="hi"),
        ]
        assert has_pairing_violations(msgs) is True

        # Public-API adaptation: validate_and_heal_messages mutates
        # the list IN PLACE and returns the ToolPairingHealReport
        # (not the healed list), so idempotence is pinned by
        # deep-snapshotting the list after each heal and comparing
        # the snapshots.
        validate_and_heal_messages(msgs, instance_short="gate-g5-pass1")
        after_first = copy.deepcopy(msgs)
        assert has_pairing_violations(msgs) is False

        report2 = validate_and_heal_messages(msgs, instance_short="gate-g5-pass2")
        after_second = copy.deepcopy(msgs)

        # Second heal is a no-op: identical length and contents.
        assert len(after_second) == len(after_first)
        assert after_second == after_first
        assert report2.synthesized == []
        assert report2.removed_orphan_indices == []
        assert report2.removed_duplicate_indices == []
        assert report2.removed_message_ids == []
        assert has_pairing_violations(msgs) is False

    def test_existing_synth_ids_not_flagged(self):
        # Literal gate shape: SystemMessages carrying synth-family
        # ids are inert non-tool messages — nothing to flag.
        msgs: list = [
            SystemMessage(content="sys", id="partner-synth-abc"),
            AIMessage(content="", tool_calls=[_tc("call_p")]),
            ToolMessage(content="real result", tool_call_id="call_p"),
            SystemMessage(content="sys2", id="pairing-synth-def"),
        ]
        assert has_pairing_violations(msgs) is False

        # Stronger recognition pin: parentless placeholder
        # ToolMessages carrying BOTH synth-id prefixes are NOT
        # flagged as stranded — _is_partner_synth recognizes
        # ``partner-synth-`` (this module) and ``pairing-synth-``
        # (the in-graph guard) alike, so a re-heal across the
        # helper chain never re-mints or re-removes them.
        parentless: list = [
            SystemMessage(content="sys"),
            ToolMessage(
                content=PARTNER_SYNTH_TEXT,
                tool_call_id="call_gone",
                id="partner-synth-call_gone",
            ),
            ToolMessage(
                content=PARTNER_SYNTH_TEXT,
                tool_call_id="call_also_gone",
                id="pairing-synth-call_also_gone",
            ),
        ]
        assert has_pairing_violations(parentless) is False
