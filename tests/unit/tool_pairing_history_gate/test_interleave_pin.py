"""G2 gate pin — SystemMessage-interleave tool-pairing violation.

Pins the gate boundary contract of
:mod:`daemon.tool_pairing_history` (2013-bricking bug class,
incident 03d7657f) for the interleave shape where a
``SystemMessage`` (not the already-pinned ``HumanMessage``) sits
between an ``AIMessage(tool_calls)`` and its ``ToolMessage``
partner.

COVERAGE NOTE: tests/unit/tool_pairing_history/test_full_history_heal.py
already covers this surface deeply (44 tests incl.
``test_human_interleave_subshape`` pinning the Human-interleave
variant and ``test_valid_multicall_block_still_valid`` pinning the
no-op path). This gate file ships thin boundary smokes so a
regression at the gate surface is caught without loading the full
regression suite.
"""

from __future__ import annotations

import copy

from langchain_core.messages import (
    AIMessage,
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


class TestSystemInterleavePin:
    """``[Sys][AI(X)][Sys][TM(X)]`` — the SystemMessage breaks the
    immediate adjacency between the issuing AIMessage and its
    ToolMessage answer (strict-gateway 2013 shape)."""

    def test_system_interleave_flags_and_heals(self):
        msgs: list = [
            SystemMessage(content="sys"),
            AIMessage(content="", tool_calls=[_tc("call_x")]),
            SystemMessage(content="sys-intervening"),
            ToolMessage(content="r", tool_call_id="call_x"),
        ]

        # Pre-state: probe flags the adjacency violation.
        assert has_pairing_violations(msgs) is True

        report = validate_and_heal_messages(msgs, instance_short="gate-g2")

        # A partner-synth-family placeholder was inserted.
        assert len(report.synthesized) == 1
        synth = report.synthesized[0]
        assert synth.tool_call_id == "call_x"
        assert synth.id.startswith("partner-synth-") or synth.id.startswith(
            "pairing-synth-"
        )
        assert synth.content == PARTNER_SYNTH_TEXT

        # The placeholder sits IMMEDIATELY after the issuing AIMessage.
        i_ai = next(i for i, m in enumerate(msgs) if isinstance(m, AIMessage))
        assert isinstance(msgs[i_ai + 1], ToolMessage)
        assert msgs[i_ai + 1].id == synth.id

        # The original misplaced TM was removed (Phase 2 orphan
        # removal — its nearest preceding non-Tool is the intervening
        # SystemMessage, which issued nothing).
        non_synth_tms = [
            m
            for m in msgs
            if isinstance(m, ToolMessage) and m.id != synth.id
        ]
        assert non_synth_tms == []

        # Post-state: probe is clean.
        assert has_pairing_violations(msgs) is False

    def test_valid_multicall_after_system_is_noop(self):
        """``[Sys][AI(a,b)][TM(a)][TM(b)]`` — valid adjacent block;
        the healer must be a strict no-op."""
        msgs: list = [
            SystemMessage(content="sys"),
            AIMessage(
                content="",
                tool_calls=[_tc("call_a"), _tc("call_b")],
            ),
            ToolMessage(content="r1", tool_call_id="call_a"),
            ToolMessage(content="r2", tool_call_id="call_b"),
        ]
        original = copy.deepcopy(msgs)

        assert has_pairing_violations(msgs) is False

        report = validate_and_heal_messages(msgs, instance_short="gate-g2b")

        # No-op: nothing synthesized or removed; list unchanged
        # (length and contents identical vs the deep copy).
        assert report.synthesized == []
        assert report.removed_orphan_indices == []
        assert report.removed_duplicate_indices == []
        assert report.removed_message_ids == []
        assert len(msgs) == len(original)
        assert msgs == original
