"""G3 — tool-pairing original-symptom closure mock test (incident 03d7657f).

Branch: ``fix/tool-pairing-full-history-heal``. Commission gate G3.

Reproduces the EXACT 03d7657f forensic shape — an UNANSWERED ``tool_call``
poisoned MID-LIST (~index 148 of ~600) reaching the LLM gateway — against
the REAL ``daemon.graph.create_agent_node`` via an in-process strict-gateway
fake LLM. The fake genuinely validates call→result IMMEDIATE ADJACENCY
(the gateway rule the count-pairing guards missed), NOT count-pairing.

Harness pattern (mirrors ``tests/unit/tool_pairing_history/test_w2_wiring.py``
``TestClassifierToW2Seam`` — the production-faithful seam): the raw fake
provider raises the canonical 2013-shaped ``openai.BadRequestError``; the
PRODUCTION classifier wrapper ``daemon.graph.classify_llm_errors`` (the same
unconditional wrap ``build_instance_llms`` applies) converts it to
``ToolPairingInvalidError`` so the agent_node W2 catch routes it. The wrapped
runnable is injected as the first positional arg of ``create_agent_node``
(``compactor=None, graph_ref=[None], config={"configurable": {"thread_id":
"g3-<scenario>"}}, llm_config={"model": "gpt-4o"}``).

Scenarios (per ``.agents/tester/MOCK_TESTS.md`` G3 spec):

  * S1 — symptom reproduction with the W1 probe DISABLED via
    ``monkeypatch.setattr("daemon.graph.has_pairing_violations", lambda m:
    False)``. Monkeypatch TARGET DOCUMENTATION: the graph calls the name AS
    IMPORTED INTO ``daemon.graph`` (import at ``daemon/graph.py:31``; the
    probe call sits at ``:566`` inside ``_ensure_full_history_pairing``, def
    ``:468``; the probe is invoked from the agent-node W1 site ``~:9049``
    AND the W2 reactive-heal site ``~:9107``). One seam therefore disables
    BOTH heals — the pre-fix configuration. The poisoned payload reaches the
    gateway, which rejects; W2's heal is a no-op (same probe seam), so the
    bounded no-heal retry fires once more and the terminal
    ``ToolPairingInvalidError`` surfaces out of the node — the brick
    symptom. INVOCATION COUNT NOTE: 2 (heal-once + retry-once W2 bound),
    not 1 — W2 stays wired in S1; the assertion target is the REJECTION,
    which fires on genuinely poisoned (unhealed) payloads.

  * S2 — W1 pre-heal delivery (healing enabled, default): same poisoned
    history → the strict gateway receives a CLEAN payload
    (``has_pairing_violations(received) is False``), EXACTLY 1 invoke,
    OK response. DEVIATION NOTE vs spec wording: the implemented W1 heal
    op for a mid-list unanswered call is heal-op (a) SYNTHESIZE-PARTNER —
    a placeholder ``ToolMessage`` with id ``partner-synth-{tc_id}``
    inserted IMMEDIATELY adjacent to the issuing AI — the orphan AI is
    NOT removed (see ``daemon/tool_pairing_history.py`` heal ops). This
    test pins the real contract (synth partner adjacent + no residual
    violations), not the spec's "orphan AI removed" phrasing.

  * S3 — W2 heal-once + single retry ("late poison"): fake in
    fail-once-then-succeed mode. The first rejection is CALL-COUNT-based
    (the post-W1 payload is asserted CLEAN first), which is the documented
    W2 motivating case — "the gateway flags a violation the proactive W1
    heal did not cover" (daemon/graph.py W2 comment). Asserts EXACTLY 2
    invokes, both payloads valid, node returns OK, no non-retryable brick.

  * S4 — W2 bounded reraise: fake ALWAYS rejects. Asserts EXACTLY 2
    invokes (heal-once + invoke-once bound), terminal exception is
    ``ToolPairingInvalidError`` with the full ``__cause__`` chain
    preserved (second rejection raised ``from`` the first per the W2
    contract), and the terminal error carries the pairing signature —
    classified via ``daemon.llm_error_classifier._matches_pairing_invalid``
    and NOT a ``TRANSIENT_EXCEPTIONS`` member (tenacity never auto-retries).

Sentinel rule: the adjacency walk skips ONLY messages whose id starts with
``partner-synth-``/``pairing-synth-`` when they are NOT ToolMessages
(adjacency-transparent sentinels, per spec). Synthesized placeholder
``ToolMessage``s carrying those ids DO count as legitimate answers — they
carry the exact ``tool_call_id`` and are what makes the healed payload
adjacency-valid.

In-process only: no daemon boot, no real LLM, no network, no port binding.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

# Canonical 2013 body prefix — mirrors
# tests/unit/tool_pairing_history/test_w2_wiring.py:74
# (_make_pairing_invalid_bad_request). Redefined locally so this
# integration file stays self-contained.
_CANONICAL_2013 = (
    "openai: invalid params, tool call result does not follow tool call (2013)"
)

# The forensic orphan tool_call id (mid-list, unanswered anywhere).
TC_ID = "g3-orphan-call-148"

POISON_INDEX = 148  # ~148 of ~600 (state coords; full_messages is +1 for the system prompt)
TOTAL_FILLER = 600


# ---------------------------------------------------------------------------
# Helpers (patterns reused from tests/unit/tool_pairing_history/test_w2_wiring.py)
# ---------------------------------------------------------------------------


def _tc(tc_id: str, name: str = "tool") -> dict:
    """Minimal tool-call dict in langchain_core contract shape
    (mirrors test_w2_wiring.py:64)."""
    return {
        "id": tc_id,
        "name": name,
        "args": {"x": 1},
        "type": "tool_call",
    }


def _make_pairing_invalid_bad_request(message: str = _CANONICAL_2013):
    """Canonical 2013-shaped ``openai.BadRequestError`` body (mirrors
    test_w2_wiring.py:74)."""
    from openai import BadRequestError

    return BadRequestError(
        message=message,
        response=MagicMock(),
        body=None,
    )


def _build_poisoned_history(
    total: int = TOTAL_FILLER,
    poison_index: int = POISON_INDEX,
    tc_id: str = TC_ID,
) -> list[BaseMessage]:
    """Forensic shape: ~600 alternating System/Human messages with an
    ``AIMessage(tool_calls=[X])`` spliced in at ~index 148 followed by
    ~250 unrelated messages and NO ToolMessage for X anywhere."""
    filler: list[BaseMessage] = []
    for k in range(total):
        if k % 2 == 0:
            filler.append(SystemMessage(content=f"g3 filler system {k}"))
        else:
            filler.append(HumanMessage(content=f"g3 filler human {k}"))
    history = list(filler)
    history.insert(poison_index, AIMessage(content="", tool_calls=[_tc(tc_id)]))
    return history


def _is_synth_sentinel(msg: BaseMessage) -> bool:
    """Adjacency-transparent sentinel: id carries the partner-synth /
    pairing-synth prefix (recognized set mirrors
    ``daemon.tool_pairing_history._is_partner_synth``)."""
    mid = getattr(msg, "id", None)
    return isinstance(mid, str) and (
        mid.startswith("partner-synth-") or mid.startswith("pairing-synth-")
    )


def _tool_call_ids(msg: BaseMessage) -> list[str]:
    ids: list[str] = []
    for tc in getattr(msg, "tool_calls", None) or []:
        if isinstance(tc, dict):
            ids.append(tc.get("id", "") or "")
        else:
            ids.append(getattr(tc, "id", "") or "")
    return ids


class StrictGatewayLLM:
    """In-process strict-gateway fake LLM — a REAL class (not a bare
    MagicMock) so every ``invoke`` runs the adjacency walk.

    Adjacency rule (gateway fidelity, NOT count-pairing): for every
    ``AIMessage`` with ``tool_calls``, walking forward, the messages must be
    exactly the matching ``ToolMessage``s (any synth-sentinel messages are
    adjacency-transparent) covering every issued id before the first
    non-ToolMessage. Any deviation — orphan ToolMessage, unanswered call,
    foreign tool_call_id in the block, non-tool interleave — is a violation.

    Modes:
      * ``always_accept`` — honest gateway: rejects ONLY genuine adjacency
        violations; valid payloads get ``AIMessage("OK ...")``.
      * ``fail_once_then_succeed`` — invocation #1 rejects EVEN IF the
        payload is valid (the documented W2 motivating case: the gateway
        flags a violation the proactive W1 heal did not cover); later
        invocations validate genuinely and accept.
      * ``always_reject`` — every invocation rejects regardless of payload.

    Captures every received messages list (post-assertion surface) and
    counts invocations (S3/S4 bounds).
    """

    ALWAYS_ACCEPT = "always_accept"
    FAIL_ONCE_THEN_SUCCEED = "fail_once_then_succeed"
    ALWAYS_REJECT = "always_reject"

    def __init__(self, mode: str, tag: str = "g3"):
        self.mode = mode
        self.tag = tag
        self.invocations = 0
        self.received: list[list[BaseMessage]] = []
        self.rejections: list[str] = []
        self._lock = threading.Lock()

    # -- gateway core -----------------------------------------------------

    def _find_violation(self, messages: list[BaseMessage]) -> str | None:
        """Return the FIRST adjacency violation reason, or None."""
        i, n = 0, len(messages)
        while i < n:
            msg = messages[i]
            if isinstance(msg, AIMessage):
                needed = _tool_call_ids(msg)
                if needed:
                    pending = list(needed)
                    j = i + 1
                    while j < n and pending:
                        mj = messages[j]
                        if isinstance(mj, ToolMessage):
                            tc_id = getattr(mj, "tool_call_id", None)
                            if tc_id in pending:
                                pending.remove(tc_id)
                            else:
                                return (
                                    f"orphan/foreign ToolMessage tool_call_id="
                                    f"{tc_id!r} inside adjacent block of AI "
                                    f"issuing {needed!r} — tool call result "
                                    f"does not follow tool call (2013)"
                                )
                        elif _is_synth_sentinel(mj):
                            pass  # adjacency-transparent sentinel
                        else:
                            return (
                                f"non-tool {type(mj).__name__} interleaved "
                                f"between AIMessage(tool_calls={needed!r}) "
                                f"and its results — tool call result does "
                                f"not follow tool call (2013)"
                            )
                        j += 1
                    if pending:
                        return (
                            f"unanswered tool_call ids {pending!r} — tool "
                            f"call result does not follow tool call (2013)"
                        )
                    i = j
                    continue
            elif isinstance(msg, ToolMessage):
                return (
                    f"orphan ToolMessage tool_call_id="
                    f"{getattr(msg, 'tool_call_id', None)!r} with no "
                    f"preceding issuing AIMessage — messages with role "
                    f"'tool' must be a response to a preceeding message "
                    f"with 'tool_calls' (2013)"
                )
            i += 1
        return None

    def _reject(self, reason: str, seq: int) -> None:
        """Raise the canonical 2013-shaped BadRequestError. The PRODUCTION
        classifier wrapper (``classify_llm_errors``, applied by this
        harness) detects the signature and converts to
        ``ToolPairingInvalidError`` so the agent_node W2 catch routes it —
        verified end-to-end by every scenario here."""
        self.rejections.append(f"[attempt-{seq}] {reason}")
        raise _make_pairing_invalid_bad_request(
            f"{_CANONICAL_2013} [{reason}] [attempt-{seq}]"
        )

    # -- Runnable surface -------------------------------------------------

    def invoke(self, messages: list[BaseMessage], **kwargs) -> AIMessage:
        with self._lock:
            self.invocations += 1
            seq = self.invocations
            self.received.append(list(messages))

        violation = self._find_violation(messages)
        if violation is not None:
            self._reject(violation, seq)

        must_reject = self.mode == self.ALWAYS_REJECT or (
            self.mode == self.FAIL_ONCE_THEN_SUCCEED and seq == 1
        )
        if must_reject:
            # Gateway-side rejection of a payload this validator found
            # adjacency-clean — the "post-W1 violation" fiction (S3/S4).
            self._reject(
                "gateway flagged a post-W1 pairing violation "
                "(simulated late poison)",
                seq,
            )

        return AIMessage(
            content=f"OK ({self.tag} strict gateway accept)",
            id=f"{self.tag}-accept-{seq}",
        )


def _build_agent_node(strict: StrictGatewayLLM, thread_id: str):
    """Harness: REAL create_agent_node + PRODUCTION classifier wrapper.

    The wrap mirrors ``build_instance_llms``' unconditional
    ``classify_llm_errors`` block — the raw fake provider's 2013
    BadRequestError is converted to ``ToolPairingInvalidError`` exactly as
    in production, routing it into the agent_node W2 catch.
    """
    from daemon.graph import classify_llm_errors, create_agent_node

    wrapped = classify_llm_errors(strict)
    config = {"configurable": {"thread_id": thread_id}}
    return create_agent_node(
        wrapped,
        system_prompt="You are helpful (g3 mock).",
        compactor=None,
        graph_ref=[None],
        config=config,
        llm_config={"model": "gpt-4o"},
    )


def _last_ai_content(result: dict) -> str:
    last_ai = None
    for m in result.get("messages", []):
        if isinstance(m, AIMessage):
            last_ai = m
    assert last_ai is not None, f"no AIMessage in node result: {result!r}"
    return last_ai.content


def _find_synth_partner(payload: list[BaseMessage], tc_id: str) -> ToolMessage | None:
    for m in payload:
        if (
            isinstance(m, ToolMessage)
            and getattr(m, "tool_call_id", None) == tc_id
            and _is_synth_sentinel(m)
        ):
            return m
    return None


# ---------------------------------------------------------------------------
# G3 scenarios
# ---------------------------------------------------------------------------


class TestG3OriginalSymptomClosure:
    """Commission gate G3 — original-symptom closure (incident 03d7657f)."""

    @pytest.mark.asyncio
    async def test_s1_symptom_reproduction_w1_disabled(self, monkeypatch):
        """S1 — healing DISABLED: the poisoned payload reaches the gateway
        and the 2013 rejection FIRES terminally (pre-fix brick proven).

        Monkeypatch target documentation: the graph calls the name AS
        IMPORTED INTO ``daemon.graph`` — ``daemon/graph.py:31`` import,
        ``:566`` probe call inside ``_ensure_full_history_pairing`` (def
        ``:468``). We patch ``daemon.graph.has_pairing_violations`` (the
        graph-resolved global), NOT the ``daemon.tool_pairing_history``
        module attribute — patching the latter would be a no-op here
        because the graph resolves the name through its own module
        globals at call time.

        One seam disables BOTH the W1 pre-heal and the W2 reactive heal
        (both call the same probe), so the W2 bounded retry is a NO-HEAL
        retry: invocation count is 2 and every received payload still
        carries the unanswered call — the gateway rejection fired twice
        on genuinely poisoned payloads and the terminal
        ToolPairingInvalidError surfaced. That terminal surfacing IS the
        pre-fix brick symptom.
        """
        from daemon.llm_error_classifier import (
            TRANSIENT_EXCEPTIONS,
            ToolPairingInvalidError,
            _matches_pairing_invalid,
        )

        monkeypatch.setattr(
            "daemon.graph.has_pairing_violations", lambda m: False
        )

        state = _build_poisoned_history()
        strict = StrictGatewayLLM(StrictGatewayLLM.ALWAYS_ACCEPT, tag="g3-s1")
        agent_node = _build_agent_node(strict, "g3-s1")

        with pytest.raises(ToolPairingInvalidError) as exc_info:
            await agent_node({"messages": state})

        # Bounded W2: heal-once (no-op, probe disabled) + invoke-once retry.
        assert strict.invocations == 2, (
            f"S1: expected 2 gateway invocations (W1 disabled → heal no-op "
            f"→ W2 single no-heal retry → reraise); got {strict.invocations}"
        )

        # EVERY received payload was genuinely poisoned — the rejection
        # fired on the real forensic shape, not on a healed payload.
        for seq, payload in enumerate(strict.received, start=1):
            poison_ais = [
                m
                for m in payload
                if isinstance(m, AIMessage) and TC_ID in _tool_call_ids(m)
            ]
            assert poison_ais, (
                f"S1 attempt {seq}: unanswered tool_call {TC_ID!r} missing "
                f"from received payload — the reproduction must carry the "
                f"poison"
            )
            assert _find_synth_partner(payload, TC_ID) is None, (
                f"S1 attempt {seq}: W1 heal leaked into the payload despite "
                f"the probe being disabled — the pre-fix configuration must "
                f"deliver the UNHEALED poison"
            )

        # The rejection is the pairing signature, terminal, non-transient.
        terminal = exc_info.value
        assert isinstance(terminal, ToolPairingInvalidError)
        assert terminal.__cause__ is not None, (
            "S1: terminal error must preserve the first rejection as "
            "__cause__ (W2 full-chain reraise)"
        )
        assert terminal.original is not None
        assert _matches_pairing_invalid(str(terminal.original)) is not None, (
            "S1: terminal rejection body must carry the canonical 2013 "
            "pairing signature"
        )
        assert not isinstance(terminal, TRANSIENT_EXCEPTIONS), (
            "S1: the pairing rejection must NOT classify as transient"
        )

    @pytest.mark.asyncio
    async def test_s2_w1_preheal_delivers_clean_payload(self):
        """S2 — healing enabled (default): W1 pre-heals the poisoned
        history so the strict gateway receives a CLEAN payload on the
        FIRST (and only) invoke, and returns OK.

        Pinned heal contract (see module docstring DEVIATION NOTE): the
        orphan AI is NOT removed; heal-op (a) synthesizes a placeholder
        ToolMessage (id ``partner-synth-{tc_id}``, matching
        ``tool_call_id``) IMMEDIATELY adjacent to the issuing AI, and no
        adjacency violation remains.
        """
        from daemon.tool_pairing_history import has_pairing_violations

        state = _build_poisoned_history()
        strict = StrictGatewayLLM(StrictGatewayLLM.ALWAYS_ACCEPT, tag="g3-s2")
        agent_node = _build_agent_node(strict, "g3-s2")

        result = await agent_node({"messages": state})

        # EXACTLY 1 invoke — the heal happened BEFORE the LLM call.
        assert strict.invocations == 1, (
            f"S2: W1 must pre-heal so the gateway is invoked EXACTLY once; "
            f"got {strict.invocations}"
        )

        payload = strict.received[0]
        assert has_pairing_violations(payload) is False, (
            "S2: the payload delivered to the gateway must be "
            "adjacency-clean after the W1 pre-heal"
        )

        # Heal contract: synth partner sits IMMEDIATELY after the poison AI.
        poison_idx = next(
            i
            for i, m in enumerate(payload)
            if isinstance(m, AIMessage) and TC_ID in _tool_call_ids(m)
        )
        nxt = payload[poison_idx + 1]
        assert isinstance(nxt, ToolMessage), (
            f"S2: message immediately after the orphan AI must be the "
            f"synthesized ToolMessage; got {type(nxt).__name__}"
        )
        assert getattr(nxt, "tool_call_id", None) == TC_ID
        synth_id = getattr(nxt, "id", None) or ""
        assert synth_id.startswith("partner-synth-"), (
            f"S2: synthesized placeholder id must carry the partner-synth- "
            f"prefix; got {synth_id!r}"
        )
        assert str(nxt.content).strip() != "", (
            "S2: the synthesized placeholder must carry a non-empty honest "
            "placeholder text (never fabricated output)"
        )

        # The node returns the gateway's OK response.
        assert _last_ai_content(result) == "OK (g3-s2 strict gateway accept)"

    @pytest.mark.asyncio
    async def test_s3_w2_heal_once_single_retry_late_poison(self):
        """S3 — W2 heal-once + single retry ("late poison"): the gateway
        rejects the FIRST (post-W1, adjacency-clean) invoke — the
        documented W2 motivating case — then accepts. EXACTLY 2 invokes,
        both payloads valid, node returns OK, NO non-retryable brick."""
        from daemon.tool_pairing_history import has_pairing_violations

        state = _build_poisoned_history()
        strict = StrictGatewayLLM(
            StrictGatewayLLM.FAIL_ONCE_THEN_SUCCEED, tag="g3-s3"
        )
        agent_node = _build_agent_node(strict, "g3-s3")

        result = await agent_node({"messages": state})

        assert strict.invocations == 2, (
            f"S3: W2 contract is heal-once + retry-once → EXACTLY 2 "
            f"invocations; got {strict.invocations}"
        )

        # The first rejection was gateway-side: the post-W1 payload was
        # already adjacency-clean (the "late poison" fiction).
        for seq, payload in enumerate(strict.received, start=1):
            assert has_pairing_violations(payload) is False, (
                f"S3 attempt {seq}: payload must be valid post-W1 heal"
            )
        assert len(strict.rejections) == 1, (
            f"S3: exactly one gateway rejection expected; got "
            f"{strict.rejections!r}"
        )
        assert "[attempt-1]" in strict.rejections[0]

        # Pipeline completes — the node returns the retry's OK response.
        assert _last_ai_content(result) == "OK (g3-s3 strict gateway accept)"

    @pytest.mark.asyncio
    async def test_s4_w2_bounded_reraise_full_chain(self):
        """S4 — W2 bounded reraise: the gateway ALWAYS rejects. EXACTLY 2
        invocations (heal-once + invoke-once bound); the terminal
        exception is the SECOND ToolPairingInvalidError with the FIRST as
        ``__cause__`` (full-chain reraise), whose own ``__cause__`` is the
        first raw 2013 BadRequestError; the terminal error carries the
        pairing signature and is NOT classified transient."""
        from daemon.llm_error_classifier import (
            TRANSIENT_EXCEPTIONS,
            ToolPairingInvalidError,
            _matches_pairing_invalid,
        )
        from openai import BadRequestError

        state = _build_poisoned_history()
        strict = StrictGatewayLLM(StrictGatewayLLM.ALWAYS_REJECT, tag="g3-s4")
        agent_node = _build_agent_node(strict, "g3-s4")

        with pytest.raises(ToolPairingInvalidError) as exc_info:
            await agent_node({"messages": state})

        assert strict.invocations == 2, (
            f"S4: heal-once + invoke-once bound → EXACTLY 2 invocations; "
            f"got {strict.invocations}"
        )

        terminal = exc_info.value
        assert isinstance(terminal, ToolPairingInvalidError)

        # Full-chain reraise: terminal(=2nd TP) --cause--> 1st TP --cause--> 1st raw BadRequestError.
        first = terminal.__cause__
        assert isinstance(first, ToolPairingInvalidError), (
            f"S4: terminal __cause__ must be the FIRST "
            f"ToolPairingInvalidError; got {type(first).__name__}"
        )
        first_raw = first.__cause__
        assert isinstance(first_raw, BadRequestError), (
            f"S4: first rejection must wrap the raw 2013 BadRequestError; "
            f"got {type(first_raw).__name__}"
        )
        assert "[attempt-1]" in str(first_raw), (
            f"S4: first raw rejection body must be the attempt-1 rejection; "
            f"got {str(first_raw)!r}"
        )
        assert terminal.original is not None
        assert "[attempt-2]" in str(terminal.original), (
            f"S4: terminal error must be the SECOND (most recent) "
            f"rejection; got {str(terminal.original)!r}"
        )

        # Pairing-signature classification — NOT a transient.
        assert _matches_pairing_invalid(str(terminal.original)) is not None, (
            "S4: terminal rejection body must carry the 2013 pairing "
            "signature"
        )
        assert not isinstance(terminal, TRANSIENT_EXCEPTIONS), (
            "S4: ToolPairingInvalidError must NOT classify as transient "
            "(tenacity never auto-retries it; W2 owns the bounded retry)"
        )
        assert ToolPairingInvalidError not in TRANSIENT_EXCEPTIONS
