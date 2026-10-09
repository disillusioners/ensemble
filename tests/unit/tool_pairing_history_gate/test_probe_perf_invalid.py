"""G4 gate pin (round 2) — O(n) probe performance + strict non-mutation
on histories that carry ``AIMessage.invalid_tool_calls`` entries.

This is the round-2 complement to ``test_probe_perf.py`` (valid-only).
The probe now unions ``tool_calls`` + ``invalid_tool_calls`` ids via
``daemon.tool_pairing_history._extract_tool_call_ids`` (incident
03d7657f round-2 fix), so the same ms-class budget must hold when
the history is peppered with invalid calls answered by an
immediately-adjacent ``ToolMessage``.

The three contracts pinned here:

  1. Cost on a ~700-msg history WITH invalid entries stays under 10s
     (reviewer observed ~0.65ms / 1000-msg on the round-1 pin; 10s
     is the gate's deliberately-generous CI-runnable ceiling per
     the gate spec).
  2. Purity: the probe does NOT mutate, replace, or reorder the
     caller's list — same object identity, same length, deep-equal
     contents afterwards, EVEN when the history contains
     ``invalid_tool_calls`` entries.
  3. Scale: at 1000-msg with a mix of valid + invalid answered
     calls, the probe stays sub-200ms (the nice-to-have target
     is 50ms; reviewer's ~0.65ms / 1000-msg is the reference
     class — the actual measured value is printed for future
     tightening of the gate spec).
"""

from __future__ import annotations

import copy
import time
import uuid

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    ToolMessage,
)

from daemon.tool_pairing_history import has_pairing_violations

# ---- test parameters ----------------------------------------------------

N_PAIRS_LARGE = 700  # round-2 complement to the round-1 678-msg pin
N_PAIRS_SCALE = 1000  # ms-class scale check
PROBE_BUDGET_SECONDS = 10.0  # gate spec CI-runnable ceiling
PROBE_HARD_BOUND_MS = 200.0  # generous hard bound for 1000-msg scale
INVALID_EVERY = 50  # every ~50th position carries an invalid block


# ---- fixture helpers ----------------------------------------------------


def _itc(tc_id: str, name: str = "bad", args: str = "{broken") -> dict:
    """Build a minimal invalid_tool_call dict in langchain_core
    contract shape. Mirrors the shape used in
    ``tests/unit/tool_pairing_history/test_full_history_heal.py``'s
    ``_itc`` (intentionally inlined here rather than imported —
    test code should not import test code).

    The ``type`` field is the wire-level marker for malformed
    calls (``"invalid_tool_call"``) and the ``error`` field is
    populated the same way langchain_core's OpenAI parser does.
    """
    return {
        "id": tc_id,
        "name": name,
        "args": args,
        "type": "invalid_tool_call",
        "error": "Failed to parse tool call: arguments were not valid JSON",
    }


def _valid_tc(tc_id: str, name: str = "good") -> dict:
    """Build a minimal well-formed tool-call dict for the
    1000-msg scale mix."""
    return {
        "id": tc_id,
        "name": name,
        "args": {},
        "type": "tool_call",
    }


def _build_700_msg_with_invalids(
    unanswered_inv_idx: int | None = None,
) -> list:
    """Build exactly 700 messages of alternating Human/AI plus
    ~13 invalid blocks (each = AIMessage(invalid_tool_calls) +
    ToolMessage answering). When ``unanswered_inv_idx`` is given,
    that block's ToolMessage is OMITTED — the invalid call
    becomes unanswered and the probe must flag it.

    Structure: a "regular" alternating Human/AI stream is built
    one slot at a time; every 50th position (offset > 0) gets
    an invalid block (AI + TM) instead. Net 700 messages, 13
    blocks (positions 50, 100, ..., 650), and the remainder
    alternating.
    """
    msgs: list = []
    inv_idx = 0
    while len(msgs) < N_PAIRS_LARGE:
        slot_is_block_slot = (
            len(msgs) > 0
            and len(msgs) % INVALID_EVERY == 0
            and (N_PAIRS_LARGE - len(msgs)) >= 2
        )
        if slot_is_block_slot:
            tc_id = f"inv_{inv_idx}"
            msgs.append(
                AIMessage(
                    content="",
                    tool_calls=[],
                    invalid_tool_calls=[_itc(tc_id)],
                    id=str(uuid.uuid4()),
                )
            )
            if inv_idx != unanswered_inv_idx:
                msgs.append(
                    ToolMessage(
                        content="answer",
                        tool_call_id=tc_id,
                        id=str(uuid.uuid4()),
                    )
                )
            inv_idx += 1
        else:
            if len(msgs) % 2 == 0:
                msgs.append(
                    HumanMessage(
                        content=f"human-{len(msgs)}",
                        id=str(uuid.uuid4()),
                    )
                )
            else:
                msgs.append(
                    AIMessage(
                        content=f"ai-{len(msgs)}",
                        id=str(uuid.uuid4()),
                    )
                )
    return msgs


def _build_1000_msg_mixed() -> list:
    """Build exactly 1000 messages mixing valid + invalid tool
    calls, all answered. The mix density: every 20th AI carries
    an invalid call answered by a ToolMessage in the
    immediately-adjacent block; every 40th AI carries a valid
    call (also answered). 40th overrides 20th so the two sets
    are disjoint.
    """
    msgs: list = []
    ai_count = 0
    inv_counter = 0
    val_counter = 0
    next_is_human = True
    while len(msgs) < N_PAIRS_SCALE:
        if next_is_human:
            msgs.append(
                HumanMessage(
                    content=f"h-{len(msgs)}",
                    id=str(uuid.uuid4()),
                )
            )
            next_is_human = False
            continue
        # AI step
        ai_count += 1
        remaining = N_PAIRS_SCALE - len(msgs)
        if ai_count % 40 == 0 and remaining >= 2:
            # 40th AI: valid call (overrides invalid rule)
            tc_id = f"call_{val_counter}"
            msgs.append(
                AIMessage(
                    content="",
                    tool_calls=[_valid_tc(tc_id)],
                    id=str(uuid.uuid4()),
                )
            )
            msgs.append(
                ToolMessage(
                    content="answer",
                    tool_call_id=tc_id,
                    id=str(uuid.uuid4()),
                )
            )
            val_counter += 1
        elif ai_count % 20 == 0 and remaining >= 2:
            # 20th AI: invalid call
            tc_id = f"inv_{inv_counter}"
            msgs.append(
                AIMessage(
                    content="",
                    tool_calls=[],
                    invalid_tool_calls=[_itc(tc_id)],
                    id=str(uuid.uuid4()),
                )
            )
            msgs.append(
                ToolMessage(
                    content="answer",
                    tool_call_id=tc_id,
                    id=str(uuid.uuid4()),
                )
            )
            inv_counter += 1
        else:
            msgs.append(
                AIMessage(
                    content=f"ai-{len(msgs)}",
                    id=str(uuid.uuid4()),
                )
            )
        next_is_human = True
    return msgs


# ---- the actual gate pins ----------------------------------------------


class TestProbePerfInvalid:
    def test_probe_invalid_mixed_700_messages_fast(self):
        """700-msg history peppered with answered invalid calls.

        Every ~50th position carries an
        ``AIMessage(tool_calls=[], invalid_tool_calls=[{id, name,
        args:{broken}])`` immediately followed by a
        ``ToolMessage(tool_call_id=...)`` answering it (all
        answered → CLEAN history).

        Cost contract: probe stays under 10s. Reviewer observed
        ~0.65ms / 1000-msg on the round-1 pin; 10s is the
        gate's deliberately-generous CI-runnable ceiling.

        Purity contract: probe does NOT mutate the caller's list
        — same object identity, same length, deep-equal contents
        afterwards (the round-1 contract extended to histories
        that contain ``invalid_tool_calls`` entries).
        """
        msgs = _build_700_msg_with_invalids()
        assert len(msgs) == N_PAIRS_LARGE

        # Sanity: fixture did seed some invalid blocks.
        invalid_count = sum(
            1
            for m in msgs
            if isinstance(m, AIMessage) and m.invalid_tool_calls
        )
        assert invalid_count > 0, (
            "fixture bug: no AIMessage carried invalid_tool_calls"
        )

        snapshot = copy.deepcopy(msgs)  # content baseline
        original_msgs = msgs  # identity baseline (same object)

        start = time.perf_counter()
        result = has_pairing_violations(original_msgs)
        elapsed = time.perf_counter() - start
        elapsed_ms = elapsed * 1000.0

        assert elapsed < PROBE_BUDGET_SECONDS, (
            f"has_pairing_violations took {elapsed:.3f}s over "
            f"{N_PAIRS_LARGE} messages with invalid entries "
            f"(budget {PROBE_BUDGET_SECONDS}s)"
        )

        # All invalid calls in this fixture are answered by an
        # immediately-adjacent ToolMessage, so the history is
        # CLEAN and the probe must return False.
        assert result is False, (
            "expected CLEAN history (all invalid calls answered) "
            "but probe flagged violations"
        )

        # Non-mutation: same object, same length, deep-equal
        # contents — the round-1 purity contract extended to
        # histories that contain invalid_tool_calls entries.
        assert msgs is original_msgs
        assert len(msgs) == N_PAIRS_LARGE
        assert msgs == snapshot

        # REVIEWER NOTE: observed ms-class on this fixture is
        # expected to be well under 1ms (round-1 reviewer
        # measured ~0.65ms / 1000-msg; this fixture is smaller
        # and lighter per-message). The 10s budget is the gate
        # ceiling, not the expected number.
        print(
            f"\n[probe-perf-invalid] 700-msg w/ invalid entries: "
            f"{elapsed_ms:.3f}ms (gate budget "
            f"{PROBE_BUDGET_SECONDS * 1000:.0f}ms)"
        )

    def test_probe_flags_unanswered_invalid_at_scale(self):
        """Same 700-msg base, but the FIRST invalid block is
        left UNANSWERED (no ToolMessage follows the AI).

        Contract: probe returns True; cost stays under 10s
        (O(n), no pathological allocation on the violation
        path); history length is unchanged (probe is read-only
        even when flagging).
        """
        UNANSWERED = 0
        msgs = _build_700_msg_with_invalids(unanswered_inv_idx=UNANSWERED)
        assert len(msgs) == N_PAIRS_LARGE

        # Sanity: confirm the first invalid call really is
        # unanswered in the fixture.
        first_inv_id = f"inv_{UNANSWERED}"
        answered = any(
            isinstance(m, ToolMessage) and m.tool_call_id == first_inv_id
            for m in msgs
        )
        assert not answered, (
            f"fixture bug: {first_inv_id} was answered but "
            f"unanswered_inv_idx={UNANSWERED} was requested"
        )

        snapshot_len = len(msgs)
        original_msgs = msgs

        start = time.perf_counter()
        result = has_pairing_violations(original_msgs)
        elapsed = time.perf_counter() - start
        elapsed_ms = elapsed * 1000.0

        assert elapsed < PROBE_BUDGET_SECONDS, (
            f"has_pairing_violations took {elapsed:.3f}s flagging "
            f"an unanswered invalid call (budget "
            f"{PROBE_BUDGET_SECONDS}s)"
        )

        # The probe must detect the unanswered invalid call.
        assert result is True, (
            "probe failed to flag the unanswered invalid_tool_call"
        )

        # Probe is read-only even when flagging: same object,
        # same length as before the call.
        assert msgs is original_msgs
        assert len(msgs) == snapshot_len
        assert len(msgs) == N_PAIRS_LARGE

        print(
            f"\n[probe-perf-invalid] 700-msg w/ 1 unanswered "
            f"invalid: {elapsed_ms:.3f}ms (gate budget "
            f"{PROBE_BUDGET_SECONDS * 1000:.0f}ms)"
        )

    def test_probe_ms_class_per_thousand(self):
        """1000-msg scale check: alternating Human/AI mixed
        with valid + invalid answered calls.

        Density: every 20th AI carries an invalid call
        answered; every 40th AI carries a valid call answered.
        40th overrides 20th so the two sets are disjoint.

        Cost contract: 50ms is the NICE-TO-HAVE target; 200ms
        is the generous hard bound the gate spec commits to.
        Reviewer's 0.65ms / 1000-msg measurement is the
        reference class — the actual measured value is printed
        for future tightening of the gate spec.
        """
        msgs = _build_1000_msg_mixed()
        assert len(msgs) == N_PAIRS_SCALE

        # Sanity: fixture has BOTH valid and invalid calls.
        invalid_count = sum(
            1
            for m in msgs
            if isinstance(m, AIMessage) and m.invalid_tool_calls
        )
        valid_count = sum(
            1
            for m in msgs
            if isinstance(m, AIMessage) and m.tool_calls
        )
        assert invalid_count > 0, "fixture bug: no invalid calls"
        assert valid_count > 0, "fixture bug: no valid calls"

        # REVIEWER REFERENCE: ~0.65ms / 1000-msg (single-digit
        # ms class). We pin a generous 200ms hard bound and
        # print the actual number so the gate spec can be
        # tightened over time.
        start = time.perf_counter()
        result = has_pairing_violations(msgs)
        elapsed = time.perf_counter() - start
        elapsed_ms = elapsed * 1000.0

        assert elapsed_ms < PROBE_HARD_BOUND_MS, (
            f"has_pairing_violations took {elapsed_ms:.3f}ms "
            f"over {N_PAIRS_SCALE} mixed messages (hard bound "
            f"{PROBE_HARD_BOUND_MS}ms)"
        )

        # All calls in this fixture are answered; history is
        # CLEAN.
        assert result is False, (
            "expected CLEAN 1000-msg history but probe flagged "
            "violations"
        )

        print(
            f"\n[probe-perf-invalid] 1000-msg scale check: "
            f"{elapsed_ms:.3f}ms (hard bound "
            f"{PROBE_HARD_BOUND_MS:.0f}ms; reviewer reference "
            f"~0.65ms/1000-msg)"
        )
