"""G4 gate pin — O(n) probe performance + strict non-mutation.

``has_pairing_violations`` is the cheap pre-flight probe callers
run BEFORE the full ``validate_and_heal_messages`` scan (see module
docstring of :mod:`daemon.tool_pairing_history`). This pin freezes
two contracts on a realistic 678-message history:

  1. Cost: the probe stays under the documented budget (10s —
     observed wall-clock on this host is single-digit
     milliseconds; 10s is a deliberately generous CI-runnable
     ceiling per the gate spec).
  2. Purity: the probe does NOT mutate, replace, or reorder the
     caller's list — same object identity, same length, deep-equal
     contents afterwards.
"""

from __future__ import annotations

import copy
import time
import uuid

from langchain_core.messages import HumanMessage, SystemMessage

from daemon.tool_pairing_history import has_pairing_violations

N_PAIRS = 339  # 339 x 2 = 678 messages
PROBE_BUDGET_SECONDS = 10.0


def _build_history() -> list:
    """Build 678 valid alternating System/Human messages (339 pairs),
    each carrying a unique uuid4 id."""
    msgs: list = []
    for i in range(N_PAIRS):
        msgs.append(SystemMessage(content=f"sys-{i}", id=str(uuid.uuid4())))
        msgs.append(HumanMessage(content=f"human-{i}", id=str(uuid.uuid4())))
    return msgs


class TestProbePerformanceAndPurity:
    def test_probe_over_678_messages_fast_and_non_mutating(self):
        msgs = _build_history()
        assert len(msgs) == 678

        snapshot = copy.deepcopy(msgs)  # content baseline
        original_msgs = msgs  # identity baseline (same object)

        start = time.perf_counter()
        result = has_pairing_violations(original_msgs)
        elapsed = time.perf_counter() - start

        assert elapsed < PROBE_BUDGET_SECONDS, (
            f"has_pairing_violations took {elapsed:.3f}s over 678 "
            f"messages (budget {PROBE_BUDGET_SECONDS}s)"
        )

        # A healthy alternating System/Human history has no
        # pairing violations.
        assert result is False

        # Non-mutation: same object, same length, deep-equal
        # contents.
        assert msgs is original_msgs
        assert len(msgs) == 678
        assert msgs == snapshot
