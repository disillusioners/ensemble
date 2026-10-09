"""V2 — tool-pairing original-symptom closure: invalid_tool_calls shape
(round-2 criterion, task 10816; incident 03d7657f round 2).

Branch: ``fix/tool-pairing-invalid-tool-calls``. Commission gate: round-2
item 2 — THE round-2 closure criterion. Companion to the round-1 artifact
``tests/integration/test_tool_pairing_original_symptom.py`` (G3, v1) —
v1 is NOT modified here and must stay green as the round-1 regression.

THE ROUND-2 SHAPE. The live incident (v0.18.4, task 10816) poisoned the
history with an ``AIMessage(tool_calls=[], invalid_tool_calls=[X])`` —
the call id was emitted on the OpenAI wire but marked INVALID (malformed
args), so langchain routed it to ``invalid_tool_calls``, NOT
``tool_calls``. Round-1 code read ONLY ``tool_calls``, so the id was
invisible to the probe / heal / ownership tracking: a DB-repair
synthesized ``ToolMessage`` answering X was classified "stranded" and
STRIPPED, re-shipping the unanswered X to the strict gateway → 2013
rejection → W2 no-heal retry → brick loop.

TWO-TIER EVIDENCE BASIS (verbatim-framed, per the round-2 spec in
``.agents/tester/MOCK_TESTS.md``; mirrored from the production docstring
at ``daemon/tool_pairing_history.py:230-247``, symbol anchor
``_extract_tool_call_ids``):

  * ROLE-level rule — CORPUS-PINNED: that a message with ``role='tool'``
    must answer a PRECEDING message carrying tool calls is pinned by the
    strict-gateway ``SIGNATURES`` corpus in
    ``daemon.llm_error_classifier.ToolPairingInvalidError`` (the corpus
    is the empirical record of the gateway's actual rejection bodies).

  * ID-level rule — EMPIRICAL OPENAI-WIRE ASSUMPTION, NOT CORPUS-PINNED:
    that EVERY emitted id — well-formed OR invalid — requires its own
    answer in the IMMEDIATELY-adjacent block is an assumption about the
    wire contract (the gateway counts an emitted-but-invalid id among
    the ids it demands answers for). It is NOT derivable from the
    SIGNATURES corpus alone. See
    ``daemon/tool_pairing_history.py:230-247`` (``_extract_tool_call_ids``
    docstring) for the production statement of the two tiers.

HARNESS DELTA (the ONLY gateway-code change vs v1). v1's
``_tool_call_ids`` (tests/integration/test_tool_pairing_original_symptom.py:154-163)
iterated ONLY ``tool_calls``. The v2 gateway's needed-set is the UNION of
``tool_calls`` AND ``invalid_tool_calls`` — sourced from the PRODUCTION
helper ``daemon.tool_pairing_history._extract_tool_call_ids`` so the mock
validates exactly the id set production probe/heal now tracks. Sentinel
exemption (``partner-synth-``/``pairing-synth-`` adjacency transparency)
is unchanged — the round-2 synths share the v1 ``partner-synth-*`` id
format, so the v1 ``_is_synth_sentinel`` recognizes them as-is.

ARCS (a)–(d) (per the round-2 spec):

  * (a) — Round-1 failure mechanism, demonstrated in-test WITHOUT
    production edits, then the branch-level UNREACHABLE assertion:
      (i)   the UNION gateway REJECTS the raw never-answered
            ``invalid_tool_calls`` poison with the canonical 2013 body;
      (i-ctrl) NEGATIVE CONTROL: a v1-view (tool_calls-only) gateway
            ACCEPTS the same payload — it cannot even see the poison —
            proving the union is the load-bearing delta;
      (ii)  ROUND-1-STRIP SIMULATION: starting from the DB-repaired form
            (poison answered by a uuid-id TM, the live post-repair-blob
            shape), manually removing the answering TM — what round-1
            orphan-removal did because its ownership set lacked invalid
            ids — re-exposes the unanswered X; the UNION gateway rejects
            again (the loop mechanism);
      (UNREACHABLE) on THIS branch ``has_pairing_violations(raw_poison)``
            is True → W1 heals BEFORE dispatch → after running the REAL
            ``create_agent_node`` path, NO captured payload contains an
            unanswered invalid id. The un-healed shape never ships.

  * (b) — W1 pre-heal synthesizes FOR the invalid call: real node → the
    gateway receives a clean payload where X is answered by the synth
    ``ToolMessage`` id ``partner-synth-{X}`` carrying
    ``PARTNER_SYNTH_INVALID_TEXT`` (asserted to be the INVALID flavor,
    NOT the standard ``PARTNER_SYNTH_TEXT``); EXACTLY 1 invoke; node OK.

  * (c) — W2 heal-once + single retry with identity survival: gateway
    fail-once-then-succeed; the first invoke raises the 2013-shaped
    unioned-signature ``BadRequestError``; EXACTLY 2 invokes; in the
    RETRY payload the synthesized TM is present BY IDENTITY (same id
    string ``partner-synth-{X}``, same Python object, adjacent-block
    position) and the DB-repair-style uuid-id TM is present and NOT
    stripped (object identity held); retry accepted; node OK.
    DEVIATION NOTE (spec wording "the synthesized TM from the W2 heal"):
    W1 and W2 run the SAME ``_ensure_full_history_pairing`` helper on
    the same list, so anything W2 can heal W1 already healed — a
    W2-ONLY-minted synth is unreachable by construction (the graph's
    W2 defensive comment covers exactly this: no-heal single retry).
    The mint happens in W1; the W2 pass preserves it (idempotent
    deterministic id per ``_make_synth_tool_message``). The test pins
    the same CONTRACT the spec targets: the synth survives into the
    retry payload by identity, adjacent, with the uuid TM unstripped.

  * (d) — Verbatim live shape, probe CLEAN, zero removal: the EXACT
    incident tuple —
      ``AIMessage(invalid_tool_calls=[{id:"call_8ed9e1771dca42348dfa7ca0",
      name:"do_thing", args:"{broken"}])`` answered by
      ``ToolMessage(tool_call_id="call_8ed9e1771dca42348dfa7ca0",
      id="1c2a9d4f-3b71-4f0e-9a23-deadbeef0001")`` (plain uuid,
      post-repair-blob form) — embedded mid-list in ~600 msgs.
    Asserts: ``has_pairing_violations`` is False (the uuid TM answers
    the invalid call under the union rule); after a full W1 pass through
    the REAL node the TM object survives BY IDENTITY (``is`` check on
    the captured payload element) — NOT stripped; gateway accepts;
    EXACTLY 1 invoke.

In-process only: no daemon boot, no real LLM, no network, no port
binding (port 8088 never touched).
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from openai import BadRequestError

from daemon.tool_pairing_history import (
    PARTNER_SYNTH_INVALID_TEXT,
    PARTNER_SYNTH_TEXT,
    _extract_tool_call_ids,
    has_pairing_violations,
)

# v1 harness reuse — the v1 file is NEVER modified, only imported.
from tests.integration.test_tool_pairing_original_symptom import (
    _CANONICAL_2013,
    _is_synth_sentinel,
    _last_ai_content,
    _make_pairing_invalid_bad_request,
    StrictGatewayLLM as _V1StrictGatewayLLM,
    _build_agent_node as _v1_build_agent_node,
    _find_synth_partner as _v1_find_synth_partner,
)

# ---------------------------------------------------------------------------
# Verbatim live tuple (incident 03d7657f round 2, task 10816)
# ---------------------------------------------------------------------------

LIVE_CALL_ID = "call_8ed9e1771dca42348dfa7ca0"
LIVE_TM_UUID = "1c2a9d4f-3b71-4f0e-9a23-deadbeef0001"
LIVE_INVALID_ENTRY = {
    "id": LIVE_CALL_ID,
    "name": "do_thing",
    "args": "{broken",
    "error": "…",
    "type": "invalid_tool_call",
}

TOTAL_FILLER = 600
POISON_INDEX = 148  # ~148 of ~600, matching the v1/forensic coords

# Per-arc poison ids (arc d uses the verbatim LIVE_CALL_ID).
X_A = "call_round2_poison_a"
X_B = "call_round2_poison_b"
X_C = "call_round2_poison_c"


def _invalid_entry(tc_id: str, name: str = "do_thing") -> dict:
    """Invalid-tool-call entry shape (langchain ``InvalidToolCall``
    contract: malformed args string + error marker)."""
    return {
        "id": tc_id,
        "name": name,
        "args": "{broken",
        "error": "…",
        "type": "invalid_tool_call",
    }


def _invalid_ai(tc_id: str) -> AIMessage:
    """THE round-2 poison shape: the call id lives ONLY in
    ``invalid_tool_calls`` — ``tool_calls`` is empty."""
    return AIMessage(content="", tool_calls=[], invalid_tool_calls=[_invalid_entry(tc_id)])


def _build_fillers(total: int = TOTAL_FILLER) -> list[BaseMessage]:
    filler: list[BaseMessage] = []
    from langchain_core.messages import HumanMessage, SystemMessage

    for k in range(total):
        if k % 2 == 0:
            filler.append(SystemMessage(content=f"g4 filler system {k}"))
        else:
            filler.append(HumanMessage(content=f"g4 filler human {k}"))
    return filler


def _build_raw_poison(tc_id: str) -> list[BaseMessage]:
    """Raw poison: invalid-only AI mid-list, NO answering TM anywhere."""
    history = _build_fillers()
    history.insert(POISON_INDEX, _invalid_ai(tc_id))
    return history


def _build_live_tuple_answered() -> tuple[list[BaseMessage], AIMessage, ToolMessage]:
    """DB-repaired form: the verbatim live tuple embedded mid-list —
    the invalid AI ANSWERED by the uuid-id TM (post-repair-blob shape).
    Returns (history, poison_ai, answering_tm)."""
    history = _build_fillers()
    poison_ai = AIMessage(
        content="", tool_calls=[], invalid_tool_calls=[dict(LIVE_INVALID_ENTRY)]
    )
    answering_tm = ToolMessage(
        content="[recovered pairing placeholder — result unavailable]",
        tool_call_id=LIVE_CALL_ID,
        id=LIVE_TM_UUID,
    )
    history.insert(POISON_INDEX, poison_ai)
    history.insert(POISON_INDEX + 1, answering_tm)
    return history, poison_ai, answering_tm


def _unanswered_invalid_ids(payload: list[BaseMessage]) -> list[str]:
    """Every invalid-sourced id in ``payload`` that lacks an answer in
    its issuing AI's IMMEDIATELY-adjacent ToolMessage block (union
    view). The UNREACHABLE assertion scans captured payloads with this."""
    unanswered: list[str] = []
    n = len(payload)
    for i, msg in enumerate(payload):
        if not isinstance(msg, AIMessage):
            continue
        invalid_ids = [
            e.get("id")
            for e in (getattr(msg, "invalid_tool_calls", None) or [])
            if isinstance(e, dict) and e.get("id")
        ]
        if not invalid_ids:
            continue
        # collect the ids answered in the adjacent forward block
        answered: set[str] = set()
        j = i + 1
        while j < n and isinstance(payload[j], ToolMessage):
            answered.add(getattr(payload[j], "tool_call_id", None) or "")
            j += 1
        unanswered.extend(tc for tc in invalid_ids if tc not in answered)
    return unanswered


# ---------------------------------------------------------------------------
# V2 gateway — the ONLY delta vs v1: unioned id extraction
# ---------------------------------------------------------------------------


class UnionStrictGatewayLLM(_V1StrictGatewayLLM):
    """V2 strict gateway: the needed-set is the UNION of
    ``tool_calls`` AND ``invalid_tool_calls`` ids — the same set the
    production probe/heal/ownership tracking walks
    (``daemon.tool_pairing_history._extract_tool_call_ids``). Every
    emitted id, well-formed or invalid, must be answered in the
    IMMEDIATELY-adjacent block; orphan TMs rejected; 2013-shaped
    BadRequestError on violation (v1 ``_make_pairing_invalid_bad_request``
    body builder reused). Walk logic is otherwise IDENTICAL to v1's
    ``_find_violation`` — the single changed line is the id extractor.
    """

    def _find_violation(self, messages: list[BaseMessage]) -> str | None:
        i, n = 0, len(messages)
        while i < n:
            msg = messages[i]
            if isinstance(msg, AIMessage):
                # ── THE V2 DELTA (vs v1 :208) ────────────────────────
                needed = _extract_tool_call_ids(msg)
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


def _reject_directly(gateway: UnionStrictGatewayLLM, messages: list[BaseMessage]) -> None:
    """Drive ONE gateway rejection out-of-band (no production code):
    run the adjacency walk and raise the canonical 2013 body — the same
    builder the gateway's ``invoke`` uses."""
    violation = gateway._find_violation(messages)
    assert violation is not None, "expected the union walk to find a violation"
    gateway.rejections.append(f"[direct] {violation}")
    raise _make_pairing_invalid_bad_request(
        f"{_CANONICAL_2013} [{violation}] [direct]"
    )


def _build_v2_agent_node(strict: UnionStrictGatewayLLM, thread_id: str):
    """v1 harness seam, re-tagged for v2 (g4 thread ids)."""
    return _v1_build_agent_node(strict, thread_id)


class CapturingGateway(UnionStrictGatewayLLM):
    """Arc-(c) variant: also captures each RAW raised BadRequestError so
    the test can assert the 2013-shaped unioned-signature body — the
    ``rejections`` list stores only the walk reason, not the canonical
    body prefix (v1 ``_reject`` prepends ``_CANONICAL_2013`` to the
    raised message only)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.raised_raw: list[BadRequestError] = []

    def invoke(self, messages, **kwargs):
        try:
            return super().invoke(messages, **kwargs)
        except BadRequestError as exc:
            self.raised_raw.append(exc)
            raise


# ---------------------------------------------------------------------------
# Arcs (a)–(d)
# ---------------------------------------------------------------------------


class TestRound2OriginalSymptomClosure:
    """Round-2 closure criterion — invalid_tool_calls shape (task 10816)."""

    # -- arc (a) -----------------------------------------------------------

    @pytest.mark.asyncio
    async def test_a_round1_mechanism_and_unreachability(self):
        """(a) Raw poison + round-1-strip simulation both rejected by the
        UNION gateway; v1-view negative control accepts; on THIS branch
        the un-healed shape is UNREACHABLE through the real W1 path."""
        # (i) the union gateway REJECTS the raw never-answered poison.
        raw = _build_raw_poison(X_A)
        union_gw = UnionStrictGatewayLLM(
            UnionStrictGatewayLLM.ALWAYS_ACCEPT, tag="g4-a"
        )
        with pytest.raises(BadRequestError) as exc_info:
            _reject_directly(union_gw, raw)
        assert "(2013)" in str(exc_info.value), (
            f"(a)(i): rejection must carry the canonical 2013 body; got "
            f"{str(exc_info.value)!r}"
        )
        assert f"'{X_A}'" in str(exc_info.value) or X_A in str(exc_info.value)

        # (i-ctrl) NEGATIVE CONTROL — the v1 view (tool_calls-only) is
        # BLIND to this poison: it accepts the same payload. The union
        # is the load-bearing delta; round-1 could not see the shape.
        v1_gw = _V1StrictGatewayLLM(
            _V1StrictGatewayLLM.ALWAYS_ACCEPT, tag="g4-a-ctrl"
        )
        ok = v1_gw.invoke(raw)  # must NOT raise
        assert ok.content.startswith("OK"), (
            "(a)(i-ctrl): v1-view gateway unexpectedly rejected the raw "
            "invalid-only poison — the blind-spot control broke"
        )
        assert v1_gw.rejections == []

        # (ii) ROUND-1-STRIP SIMULATION: the DB-repaired form (poison
        # answered by a uuid TM — what a prior repair surgery left in
        # the blob) survives on THIS branch, but round-1 heal's
        # ownership set lacked invalid ids, so the answering TM read as
        # stranded-orphan and was STRIPPED. Simulate exactly that strip
        # in-test (no production edits): the payload re-carries the
        # unanswered X → the union gateway rejects AGAIN (the loop
        # mechanism).
        answered, _poison_ai, _answering_tm = _build_live_tuple_answered()
        assert has_pairing_violations(answered) is False, (
            "(a)(ii): the answered live-tuple history must be clean "
            "before the simulated strip"
        )
        stripped = [
            m
            for m in answered
            if not (
                isinstance(m, ToolMessage)
                and getattr(m, "tool_call_id", None) == LIVE_CALL_ID
            )
        ]
        assert _unanswered_invalid_ids(stripped) == [LIVE_CALL_ID], (
            "(a)(ii): the strip must re-expose the unanswered invalid id"
        )
        union_gw2 = UnionStrictGatewayLLM(
            UnionStrictGatewayLLM.ALWAYS_ACCEPT, tag="g4-a-strip"
        )
        with pytest.raises(BadRequestError) as strip_exc:
            _reject_directly(union_gw2, stripped)
        assert "(2013)" in str(strip_exc.value)

        # (UNREACHABLE) on THIS branch: probe True → W1 heals BEFORE
        # dispatch → no captured payload ever carries an unanswered
        # invalid id. The un-healed shape never ships.
        assert has_pairing_violations(raw) is True, (
            "(a): the raw poison must probe dirty so W1's gate fires"
        )
        strict = UnionStrictGatewayLLM(
            UnionStrictGatewayLLM.ALWAYS_ACCEPT, tag="g4-a-node"
        )
        agent_node = _build_v2_agent_node(strict, "g4-a")
        result = await agent_node({"messages": raw})

        assert strict.invocations == 1, (
            f"(a): healing enabled → EXACTLY 1 invoke; got {strict.invocations}"
        )
        for seq, payload in enumerate(strict.received, start=1):
            assert _unanswered_invalid_ids(payload) == [], (
                f"(a) UNREACHABLE violated at attempt {seq}: captured "
                f"payload carries unanswered invalid ids "
                f"{_unanswered_invalid_ids(payload)!r}"
            )
        assert _last_ai_content(result).startswith("OK")

    # -- arc (b) -----------------------------------------------------------

    @pytest.mark.asyncio
    async def test_b_w1_synthesizes_partner_for_invalid_call(self):
        """(b) Poisoned history → real node → the strict gateway receives
        a clean payload where the invalid X is answered by the synth TM
        (id ``partner-synth-{X}``) carrying the INVALID-flavored text;
        EXACTLY 1 invoke; node OK."""
        state = _build_raw_poison(X_B)
        strict = UnionStrictGatewayLLM(
            UnionStrictGatewayLLM.ALWAYS_ACCEPT, tag="g4-b"
        )
        agent_node = _build_v2_agent_node(strict, "g4-b")

        result = await agent_node({"messages": state})

        assert strict.invocations == 1, (
            f"(b): W1 must pre-heal so the gateway is invoked EXACTLY "
            f"once; got {strict.invocations}"
        )
        payload = strict.received[0]
        assert has_pairing_violations(payload) is False, (
            "(b): the delivered payload must be adjacency-clean"
        )

        poison_idx = next(
            i
            for i, m in enumerate(payload)
            if isinstance(m, AIMessage) and X_B in _extract_tool_call_ids(m)
        )
        nxt = payload[poison_idx + 1]
        assert isinstance(nxt, ToolMessage), (
            f"(b): message immediately after the invalid AI must be the "
            f"synth TM; got {type(nxt).__name__}"
        )
        assert getattr(nxt, "tool_call_id", None) == X_B
        assert getattr(nxt, "id", None) == f"partner-synth-{X_B}", (
            f"(b): synth id must be the deterministic partner-synth-"
            f"{{tc_id}} form; got {getattr(nxt, 'id', None)!r}"
        )
        # THE round-2 flavor assertion: the INVALID text, not the
        # standard wording.
        assert str(nxt.content) == PARTNER_SYNTH_INVALID_TEXT, (
            f"(b): synth content must be PARTNER_SYNTH_INVALID_TEXT "
            f"(invalid flavor); got {str(nxt.content)!r}"
        )
        assert str(nxt.content) != PARTNER_SYNTH_TEXT, (
            "(b): the standard PARTNER_SYNTH_TEXT would mislabel the "
            "cause (the call was rejected BEFORE execution, never "
            "interrupted mid-execution)"
        )

        assert _last_ai_content(result) == "OK (g4-b strict gateway accept)"

    # -- arc (c) -----------------------------------------------------------

    @pytest.mark.asyncio
    async def test_c_w2_heal_once_retry_identity_survival(self):
        """(c) Fail-once-then-succeed: the first invoke raises the
        2013-shaped unioned-signature BadRequestError; W2 heals once and
        retries; EXACTLY 2 invokes; the synthesized TM is present in the
        RETRY payload BY IDENTITY (same id string, same Python object,
        adjacent block) and the DB-repair-style uuid-id TM is present
        and NOT stripped; retry accepted; node OK.

        DEVIATION NOTE (documented per the commission contract): the
        spec's phrase "the synthesized TM from the W2 heal" is pinned
        here as the synth CARRIED THROUGH the W2 cycle. W1 and W2 run
        the same idempotent full-history helper on the same in-place
        list, so a W2-ONLY-minted synth is unreachable by construction
        (anything healable was healed pre-dispatch; the graph's own W2
        defensive comment documents the no-heal single-retry branch).
        The deterministic ``partner-synth-{tc_id}`` id format makes the
        pinned contract — same id string in both payloads — hold
        regardless of which pass minted it.
        """
        # Scenario: the DB-repaired live tuple (uuid TM present, already
        # answered) + a SECOND, unanswered invalid poison (X_C) that W1
        # heals pre-dispatch.
        state, live_ai, live_tm = _build_live_tuple_answered()
        state.insert(POISON_INDEX + 40, _invalid_ai(X_C))
        assert has_pairing_violations(state) is True, (
            "(c): the X_C poison must probe dirty so W1 heals it"
        )

        strict = CapturingGateway(
            CapturingGateway.FAIL_ONCE_THEN_SUCCEED, tag="g4-c"
        )
        agent_node = _build_v2_agent_node(strict, "g4-c")

        result = await agent_node({"messages": state})

        assert strict.invocations == 2, (
            f"(c): W2 contract is heal-once + retry-once → EXACTLY 2 "
            f"invocations; got {strict.invocations}"
        )
        assert len(strict.rejections) == 1, (
            f"(c): exactly one gateway rejection expected; got "
            f"{strict.rejections!r}"
        )
        # The raised exception (not the reason list) carries the
        # canonical body — the 2013-shaped unioned-signature rejection.
        assert len(strict.raised_raw) == 1, (
            f"(c): exactly one raw BadRequestError expected; got "
            f"{len(strict.raised_raw)}"
        )
        assert isinstance(strict.raised_raw[0], BadRequestError)
        assert "(2013)" in str(strict.raised_raw[0]), (
            f"(c): the raised rejection must be 2013-shaped (unioned "
            f"signature); got {str(strict.raised_raw[0])!r}"
        )

        payload1, payload2 = strict.received[0], strict.received[1]

        # Both payloads adjacency-clean (W1 healed; W2 preserved).
        for seq, payload in enumerate(strict.received, start=1):
            assert has_pairing_violations(payload) is False, (
                f"(c): payload {seq} must be clean"
            )
            assert _unanswered_invalid_ids(payload) == [], (
                f"(c): payload {seq} must answer every invalid id"
            )

        # Synth identity survival across the W2 cycle: same id string in
        # BOTH payloads, and the SAME Python object.
        synth1 = _v1_find_synth_partner(payload1, X_C)
        synth2 = _v1_find_synth_partner(payload2, X_C)
        assert synth1 is not None and synth2 is not None, (
            "(c): the synthesized partner TM must appear in both payloads"
        )
        assert synth1.id == synth2.id == f"partner-synth-{X_C}", (
            f"(c): synth id string must survive identically; got "
            f"{synth1.id!r} vs {synth2.id!r}"
        )
        assert synth1 is synth2, (
            "(c): the synth must be the SAME object across the W2 cycle "
            "(in-place idempotent heal — not re-minted)"
        )
        # Same position class: immediately adjacent to its issuing AI in
        # the RETRY payload.
        ai_idx2 = next(
            i
            for i, m in enumerate(payload2)
            if isinstance(m, AIMessage) and X_C in _extract_tool_call_ids(m)
        )
        assert payload2[ai_idx2 + 1] is synth2, (
            "(c): in the retry payload the synth must sit in the "
            "immediately-adjacent block of its issuing AI"
        )

        # The DB-repair-style uuid-id TM: present and NOT stripped —
        # by object identity and by id, in BOTH payloads.
        for seq, payload in enumerate(strict.received, start=1):
            assert any(m is live_tm for m in payload), (
                f"(c): payload {seq} must retain the uuid-id TM by object "
                f"identity (round-1 would have stripped it)"
            )
            kept = [
                m
                for m in payload
                if isinstance(m, ToolMessage)
                and getattr(m, "id", None) == LIVE_TM_UUID
            ]
            assert kept and kept[0].tool_call_id == LIVE_CALL_ID, (
                f"(c): payload {seq} must retain the uuid TM answering "
                f"the live call id"
            )

        assert _last_ai_content(result) == "OK (g4-c strict gateway accept)"

    # -- arc (d) -----------------------------------------------------------

    @pytest.mark.asyncio
    async def test_d_verbatim_live_tuple_probe_clean_no_removal(self):
        """(d) The EXACT incident tuple (verbatim call id + uuid TM id),
        embedded mid-list in ~600 msgs: probe CLEAN; after a full W1
        pass through the real node the TM object survives BY IDENTITY
        (``is`` check on the captured payload element) — NOT stripped;
        gateway accepts; EXACTLY 1 invoke."""
        state, live_ai, live_tm = _build_live_tuple_answered()
        assert len(state) >= TOTAL_FILLER, (
            f"(d): history must be ~600 msgs; got {len(state)}"
        )

        # Probe CLEAN under the union rule: the uuid TM answers the
        # invalid call in the immediately-adjacent block.
        assert has_pairing_violations(state) is False, (
            "(d): the verbatim live tuple must probe CLEAN (uuid TM "
            "answers the invalid call under the union rule)"
        )

        strict = UnionStrictGatewayLLM(
            UnionStrictGatewayLLM.ALWAYS_ACCEPT, tag="g4-d"
        )
        agent_node = _build_v2_agent_node(strict, "g4-d")

        result = await agent_node({"messages": state})

        assert strict.invocations == 1, (
            f"(d): clean history → EXACTLY 1 invoke; got {strict.invocations}"
        )
        assert strict.rejections == [], (
            f"(d): the gateway must accept the live-tuple payload; got "
            f"{strict.rejections!r}"
        )

        payload = strict.received[0]
        # ZERO removal — both tuple members survive BY IDENTITY.
        assert any(m is live_tm for m in payload), (
            "(d): the uuid-id TM object must survive the full W1 pass "
            "by identity (is check) — round-1 stripped it"
        )
        assert any(m is live_ai for m in payload), (
            "(d): the invalid-call AI object must survive by identity"
        )
        kept_tm = next(
            m
            for m in payload
            if isinstance(m, ToolMessage) and m is live_tm
        )
        assert kept_tm.id == LIVE_TM_UUID
        assert kept_tm.tool_call_id == LIVE_CALL_ID
        # Adjacency preserved: the TM still sits right after its issuer.
        ai_idx = next(
            i for i, m in enumerate(payload) if m is live_ai
        )
        assert payload[ai_idx + 1] is live_tm, (
            "(d): the uuid TM must remain in the immediately-adjacent "
            "block of its issuing AI"
        )
        assert has_pairing_violations(payload) is False

        assert _last_ai_content(result) == "OK (g4-d strict gateway accept)"
