"""FIX 5 — Regression pin for the W2 reactive heal-once-retry wiring.

Branch: ``fix/tool-pairing-full-history-heal``.

The agent_node W2 catch (the primary ``except ToolPairingInvalidError``
site in :func:`daemon.graph.create_agent_node`; the FIX 1 post-compaction
inner-try inside the ``ContextLengthExceededError`` handler) heals the LLM-bound
payload when the gateway rejects with ``ToolPairingInvalidError`` and
re-invokes the LLM ONCE. A second failure reraises the second
exception with the FIRST exception as ``__cause__`` via ``from`` (the
FIX 4 alignment that preserves maximal diagnostics).

These tests pin the contract at the graph level so the W2 wiring
cannot silently regress. The test design follows the existing
``test_graph_retry_integration.py`` mock pattern (``mock_llm_with_tools``,
``mock_graph``, ``mock_compactor``, ``mock_state``) — same fixtures,
same call style. Each test class targets one W2 site:

  * ``TestW2PrimarySiteWiring`` — primary dispatch W2 (the primary
    ``except ToolPairingInvalidError`` site in
    :func:`daemon.graph.create_agent_node`). Poisoned history → first
    invoke raises ``ToolPairingInvalidError`` → W2 heal-once-retry →
    second invoke succeeds. Asserts invoke count is EXACTLY 2 and the second
    payload is healed/order-valid.

  * ``TestW2PrimarySiteReraiseChain`` — second-failure path. BOTH
    invokes raise ``ToolPairingInvalidError``; the reraise is the
    SECOND exception with the FIRST as ``__cause__`` (FIX 4
    alignment). Pin against silent regression of the comment-vs-code
    mismatch the review caught.

  * ``TestW2PostCompactionWiring`` — post-compaction W2 (the FIX 1
    inner-try inside the ``ContextLengthExceededError`` handler in
    :func:`daemon.graph.create_agent_node`; the formerly-dead sibling
    clause). First invoke raises ``ContextLengthExceededError``; the compactor
    runs; the post-compaction invoke raises
    ``ToolPairingInvalidError``; the W2 heal-once-retry catches it
    INSIDE the CLE handler (the dead-code fix); the retry succeeds.
    Asserts invoke count is 3 (1 pre-CLE + 1 post-CLE + 1 W2 retry)
    and the response is the W2-retry response, NOT a propagated
    ``ToolPairingInvalidError``.

  * ``TestW2PostCompactionReraiseChain`` — second-failure path on
    the post-compaction site. Both the post-compaction invoke AND
    the W2 retry raise ``ToolPairingInvalidError``; the reraise
    preserves the original as ``__cause__`` (FIX 1 inner-try
    pattern).
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tc(tc_id: str, name: str = "tool") -> dict:
    """Minimal tool-call dict in langchain_core contract shape."""
    return {
        "id": tc_id,
        "name": name,
        "args": {"x": 1},
        "type": "tool_call",
    }


def _make_pairing_invalid_bad_request(
    message: str = "openai: invalid params, tool call result does not follow tool call (2013)",
):
    """Build a synthetic ``openai.BadRequestError`` whose body text
    matches the canonical 2013 signature. The classifier
    (``daemon.llm_error_classifier``) detects this signature and
    raises ``ToolPairingInvalidError``. We import the exception
    classes lazily inside each test so the test file's import cost
    stays low and the import path is explicit in each test.
    """
    from openai import BadRequestError
    return BadRequestError(
        message=message,
        response=MagicMock(),
        body=None,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_llm_with_tools():
    """Fake LLM whose ``invoke`` is a MagicMock we set per-test via
    ``side_effect``."""
    mock = MagicMock()
    mock.invoke = MagicMock()
    return mock


@pytest.fixture
def mock_graph():
    """Fake compiled graph for the compactor's ``aget_state`` /
    ``aupdate_state`` calls. ``aget_state`` returns a mock state
    with the supplied ``messages``; ``aupdate_state`` returns a
    pass-through."""
    graph = MagicMock()
    graph.aget_state = AsyncMock()
    graph.aupdate_state = AsyncMock()
    return graph


@pytest.fixture
def mock_compactor():
    """Fake compactor with the minimum surface the reactive CLE site
    touches (``compact_state`` async, ``config`` MagicMock, empty
    ``llm_config``)."""
    compactor = MagicMock()
    compactor.compact_state = AsyncMock()
    compactor.config = MagicMock()
    compactor.llm_config = {}
    return compactor


@dataclass
class _MockStateValues:
    values: dict


def _make_mock_state(messages: list, compacted_at=None) -> _MockStateValues:
    """Wrap a messages list in the ``state.values`` shape the
    compactor reads."""
    return _MockStateValues(
        values={"messages": messages, "compacted_at": compacted_at},
    )


# ---------------------------------------------------------------------------
# FIX 5 — W2 PRIMARY SITE WIRING
# ---------------------------------------------------------------------------


class TestW2PrimarySiteWiring:
    """Pin the primary dispatch W2 catch (the primary
    ``except ToolPairingInvalidError`` site in
    :func:`daemon.graph.create_agent_node`).

    Fake LLM whose first invoke raises ``ToolPairingInvalidError``
    (simulating the gateway's 2013 rejection), second invoke returns
    a valid response. The W2 catch must run the heal-once, re-invoke
    ONCE, and propagate the second response. EXACTLY 2 invokes; the
    second payload is healed/order-valid.
    """

    @pytest.mark.asyncio
    async def test_w2_heal_then_succeed_invoke_count_is_two(
        self, mock_llm_with_tools, caplog
    ):
        from daemon.graph import create_agent_node
        from daemon.llm_error_classifier import ToolPairingInvalidError

        # History: a clean pairing — the W1 probe (run BEFORE the
        # first invoke) will report no violation, so the W1 heal is
        # a no-op and the first invoke is called with the
        # as-supplied payload. The fake LLM raises
        # ``ToolPairingInvalidError`` on the first invoke to
        # simulate the gateway's 2013 rejection. The W2 catch
        # re-invokes; the second invoke returns a valid response.
        clean_messages = [
            SystemMessage(content="You are helpful."),
            HumanMessage(content="hi"),
        ]
        # State.messages is the conversation history WITHOUT the
        # system prompt (the agent_node prepends it). The W1 probe
        # sees ``full_messages`` (system + state.messages).
        state_messages = clean_messages[1:]

        # Build the two-step side effect: first invoke raises
        # ``ToolPairingInvalidError``; second returns a valid
        # ``AIMessage``.
        original = _make_pairing_invalid_bad_request()
        first_exc = ToolPairingInvalidError(original, original)
        second_response = AIMessage(content="OK after W2 heal")
        mock_llm_with_tools.invoke.side_effect = [first_exc, second_response]

        # Agent node: minimal factory args (no compactor, no
        # graph_ref, no LLM extras).
        config = {"configurable": {"thread_id": "test-w2-primary"}}
        agent_node = create_agent_node(
            mock_llm_with_tools,
            system_prompt=clean_messages[0].content,
            compactor=None,
            graph_ref=[None],
            config=config,
            llm_config={"model": "gpt-4o"},
        )

        # Capture daemon.graph logs at WARNING to confirm the W2
        # path emitted its signature log line.
        import logging
        caplog.set_level(logging.WARNING, logger="daemon.graph")

        result = await agent_node({"messages": state_messages})

        # EXACTLY 2 invokes — the W1 probe is a no-op (clean
        # history), so invoke #1 is the primary, invoke #2 is the
        # W2 retry. No more.
        assert mock_llm_with_tools.invoke.call_count == 2, (
            f"W2 wiring pin: expected EXACTLY 2 invokes (primary + "
            f"W2 retry); got {mock_llm_with_tools.invoke.call_count}"
        )

        # Second invoke's payload is healed/order-valid. The
        # W1 probe runs on the payload, so we can use
        # ``has_pairing_violations`` as the cheap validator.
        from daemon.tool_pairing_history import has_pairing_violations
        second_call_args = mock_llm_with_tools.invoke.call_args_list[1]
        second_payload = second_call_args.args[0]
        assert has_pairing_violations(second_payload) is False, (
            f"W2 retry payload must be healed/order-valid; got "
            f"{second_payload}"
        )

        # The response is the second invoke's result, not an
        # exception or a fallback.
        assert "messages" in result
        assert len(result["messages"]) == 1
        # The agent_node's emitted message is the second invoke's
        # AIMessage (possibly wrapped in a list). The content
        # round-trips.
        emitted = result["messages"][0]
        # The emitted message is what ``current_llm.invoke`` returned,
        # which the agent_node threads into the C2 return. Its
        # content is "OK after W2 heal".
        assert emitted.content == "OK after W2 heal"

    @pytest.mark.asyncio
    async def test_w2_heal_does_not_run_third_invoke(
        self, mock_llm_with_tools, caplog
    ):
        """Belt-and-suspenders: verify NO third invoke fires even if
        the second succeeds. Pins the bounded-retry contract
        (heal-once + retry-once, never more)."""
        from daemon.graph import create_agent_node
        from daemon.llm_error_classifier import ToolPairingInvalidError

        state_messages = [HumanMessage(content="hi")]
        original = _make_pairing_invalid_bad_request()
        first_exc = ToolPairingInvalidError(original, original)
        second_response = AIMessage(content="OK")
        # If a third invoke fires, the third ``side_effect`` would
        # be consumed and the test would fail with StopIteration.
        # Use a long list to make the failure mode obvious.
        mock_llm_with_tools.invoke.side_effect = [
            first_exc, second_response,
            # Sentinel — if reached, the W2 wiring is unbounded.
            RuntimeError("W2 third invoke should never fire"),
        ]

        config = {"configurable": {"thread_id": "test-w2-bounded"}}
        agent_node = create_agent_node(
            mock_llm_with_tools,
            system_prompt="You are helpful.",
            compactor=None,
            graph_ref=[None],
            config=config,
            llm_config={"model": "gpt-4o"},
        )

        await agent_node({"messages": state_messages})

        # Exactly 2 invokes — never 3 or more.
        assert mock_llm_with_tools.invoke.call_count == 2, (
            f"W2 bounded-retry pin: expected EXACTLY 2 invokes; "
            f"got {mock_llm_with_tools.invoke.call_count}. The W2 "
            f"contract is heal-once + retry-once, never more."
        )


# ---------------------------------------------------------------------------
# FIX 5 — W2 PRIMARY SITE RERAISE CHAIN (FIX 4 alignment pin)
# ---------------------------------------------------------------------------


class TestW2PrimarySiteReraiseChain:
    """Pin the FIX 4 alignment: second-failure reraise preserves
    maximal diagnostics via ``raise _w2_second_exc from _w2_pairing_exc``.

    Pre-FIX 4 the code was ``raise _w2_second_exc from _w2_second_exc.__cause__``
    and the comment said "reraise the original" — the comment and
    code disagreed AND the chain lost the original's signature.
    """

    @pytest.mark.asyncio
    async def test_second_failure_preserves_original_as_cause(
        self, mock_llm_with_tools
    ):
        from daemon.graph import create_agent_node
        from daemon.llm_error_classifier import ToolPairingInvalidError

        state_messages = [HumanMessage(content="hi")]

        # Two distinct BadRequestError originals so we can identify
        # which one is the ``__cause__`` after the reraise.
        original_first = _make_pairing_invalid_bad_request(
            message="openai: invalid params, tool call result does not "
            "follow tool call (2013) [first attempt]",
        )
        original_second = _make_pairing_invalid_bad_request(
            message="openai: invalid params, tool call result does not "
            "follow tool call (2013) [second attempt]",
        )
        first_exc = ToolPairingInvalidError(original_first, "first sig")
        second_exc = ToolPairingInvalidError(original_second, "second sig")
        mock_llm_with_tools.invoke.side_effect = [first_exc, second_exc]

        config = {"configurable": {"thread_id": "test-w2-reraise"}}
        agent_node = create_agent_node(
            mock_llm_with_tools,
            system_prompt="You are helpful.",
            compactor=None,
            graph_ref=[None],
            config=config,
            llm_config={"model": "gpt-4o"},
        )

        # Second-failure path raises. The raised exception is the
        # SECOND ``ToolPairingInvalidError``; its ``__cause__`` is
        # the FIRST ``ToolPairingInvalidError`` (FIX 4 alignment).
        with pytest.raises(ToolPairingInvalidError) as exc_info:
            await agent_node({"messages": state_messages})

        raised = exc_info.value
        # Exactly 2 invokes — the W2 catch fires once, the second
        # failure reraises, no third invoke.
        assert mock_llm_with_tools.invoke.call_count == 2

        # The raised exception is the SECOND one (its signature
        # is "second sig").
        assert raised is second_exc, (
            f"W2 reraise: expected the SECOND exception to be the "
            f"primary raise; got a different object. Pre-FIX 4 this "
            f"could lose the original's chain."
        )
        # The ``__cause__`` is the FIRST exception (FIX 4
        # alignment). The original ``BadRequestError``s are still
        # accessible via ``.original`` for forensic access.
        assert raised.__cause__ is first_exc, (
            f"W2 reraise (FIX 4 alignment): expected __cause__ to be "
            f"the FIRST ToolPairingInvalidError; got {raised.__cause__!r}. "
            f"Pre-FIX 4 the chain was raise second from second.__cause__ "
            f"(lost the original)."
        )
        # The original ``BadRequestError``s are still attached.
        assert raised.original is original_second
        assert raised.__cause__.original is original_first


# ---------------------------------------------------------------------------
# FIX 5 — W2 POST-COMPACTION WIRING (FIX 1 inner-try pin)
# ---------------------------------------------------------------------------


class TestW2PostCompactionWiring:
    """Pin the post-compaction W2 inner-try (the FIX 1 inner-try
    inside the ``ContextLengthExceededError`` handler in
    :func:`daemon.graph.create_agent_node`).

    Pre-FIX 1 the post-compaction W2 was a DEAD SIBLING CLAUSE at
    the same level as the ``ContextLengthExceededError`` handler —
    Python ``except`` clauses only catch exceptions raised in the
    try BODY, so a ``ToolPairingInvalidError`` raised inside the CLE
    handler propagated OUT of the try statement, bypassing the
    dead sibling. The post-compaction invoke was effectively
    uncaught, and the strict-gateway 2013 bricked the instance.

    FIX 1 moved the W2 catch INTO the CLE handler as an inner
    ``try/except ToolPairingInvalidError``. This test pins the new
    structure by triggering the path: CLE → compaction → post-
    compaction invoke raises 2013 → W2 heal-once-retry catches it
    INSIDE the CLE handler → retry succeeds.
    """

    @pytest.mark.asyncio
    async def test_post_compaction_w2_catches_and_retries(
        self, mock_llm_with_tools, mock_graph, mock_compactor
    ):
        from daemon.compaction import CompactionResult
        from daemon.graph import create_agent_node
        from daemon.llm_error_classifier import (
            ContextLengthExceededError,
            ToolPairingInvalidError,
        )

        # Step 1 — first invoke raises CLE. Step 2 — compactor
        # returns a successful compaction. Step 3 — post-compaction
        # invoke raises ToolPairingInvalidError. Step 4 — W2
        # heal-once-retry's invoke succeeds.
        original_cle = _make_pairing_invalid_bad_request(
            message="context_length_exceeded: maximum context length is 100000",
        )
        cle = ContextLengthExceededError(original_cle, model="gpt-4o")

        compacted_messages = [
            HumanMessage(content="[compaction summary] hi"),
        ]
        compaction_result = CompactionResult(
            replacement_messages=compacted_messages,
            tokens_before=1000,
            tokens_after=500,
            tokens_saved=500,
            messages_before=10,
            messages_after=1,
            compaction_type="summarization",
            compacted_at="2024-01-01T00:00:00Z",
        )
        mock_compactor.compact_state.return_value = compaction_result
        mock_graph.aget_state.return_value = _make_mock_state(
            compacted_messages, compacted_at="2024-01-01T00:00:00Z",
        )

        original_pairing = _make_pairing_invalid_bad_request()
        post_compact_exc = ToolPairingInvalidError(original_pairing, original_pairing)
        w2_retry_response = AIMessage(content="OK after post-CLE W2 heal")
        mock_llm_with_tools.invoke.side_effect = [
            cle,                       # invoke 1 — pre-CLE invoke raises CLE
            post_compact_exc,          # invoke 2 — post-compaction invoke raises 2013
            w2_retry_response,         # invoke 3 — W2 retry succeeds
        ]

        config = {"configurable": {"thread_id": "test-w2-post-compact"}}
        agent_node = create_agent_node(
            mock_llm_with_tools,
            system_prompt="You are helpful.",
            compactor=mock_compactor,
            graph_ref=[mock_graph],
            config=config,
            llm_config={"model": "gpt-4o"},
        )

        # The post-compaction W2 catch (FIX 1) is supposed to fire
        # INSIDE the CLE handler. If FIX 1 regressed, the
        # ``ToolPairingInvalidError`` would propagate out of the
        # CLE handler, the existing error path would re-raise, and
        # this test would fail with the wrong exception type.
        result = await agent_node({"messages": []})

        # 3 invokes — 1 pre-CLE, 1 post-compaction (which raised
        # the pairing error), 1 W2 retry.
        assert mock_llm_with_tools.invoke.call_count == 3, (
            f"Post-compaction W2 wiring pin (FIX 1): expected "
            f"EXACTLY 3 invokes (pre-CLE + post-CLE + W2 retry); "
            f"got {mock_llm_with_tools.invoke.call_count}. Pre-FIX 1 "
            f"the third invoke never fired (dead sibling clause)."
        )

        # The response is the W2 retry's AIMessage (NOT a fallback
        # and NOT a propagated ToolPairingInvalidError).
        assert "messages" in result
        assert len(result["messages"]) == 1
        emitted = result["messages"][0]
        assert emitted.content == "OK after post-CLE W2 heal", (
            f"Post-compaction W2 retry response: expected the W2 "
            f"retry's AIMessage; got {emitted!r}. Pre-FIX 1 the "
            f"ToolPairingInvalidError would have propagated out and "
            f"no response would be returned."
        )


class TestW2PostCompactionReraiseChain:
    """Pin the FIX 1 + FIX 4 alignment on the post-compaction
    second-failure path. Both the post-compaction invoke AND the
    W2 retry raise ``ToolPairingInvalidError``; the reraise is the
    post-compaction second exception with the post-compaction first
    exception as ``__cause__`` (FIX 1 inner-try + FIX 4 chaining)."""

    @pytest.mark.asyncio
    async def test_post_compaction_second_failure_reraise_chain(
        self, mock_llm_with_tools, mock_graph, mock_compactor
    ):
        from daemon.compaction import CompactionResult
        from daemon.graph import create_agent_node
        from daemon.llm_error_classifier import (
            ContextLengthExceededError,
            ToolPairingInvalidError,
        )

        original_cle = _make_pairing_invalid_bad_request(
            message="context_length_exceeded",
        )
        cle = ContextLengthExceededError(original_cle, model="gpt-4o")

        compacted_messages = [HumanMessage(content="[summary]")]
        compaction_result = CompactionResult(
            replacement_messages=compacted_messages,
            tokens_before=1000, tokens_after=500, tokens_saved=500,
            messages_before=10, messages_after=1,
            compaction_type="summarization",
            compacted_at="2024-01-01T00:00:00Z",
        )
        mock_compactor.compact_state.return_value = compaction_result
        mock_graph.aget_state.return_value = _make_mock_state(
            compacted_messages, compacted_at="2024-01-01T00:00:00Z",
        )

        original_first = _make_pairing_invalid_bad_request(
            message="2013 [post-compact first]",
        )
        original_second = _make_pairing_invalid_bad_request(
            message="2013 [post-compact second]",
        )
        first_exc = ToolPairingInvalidError(original_first, "first sig")
        second_exc = ToolPairingInvalidError(original_second, "second sig")
        mock_llm_with_tools.invoke.side_effect = [
            cle,
            first_exc,
            second_exc,
        ]

        config = {"configurable": {"thread_id": "test-w2-post-reraise"}}
        agent_node = create_agent_node(
            mock_llm_with_tools,
            system_prompt="You are helpful.",
            compactor=mock_compactor,
            graph_ref=[mock_graph],
            config=config,
            llm_config={"model": "gpt-4o"},
        )

        with pytest.raises(ToolPairingInvalidError) as exc_info:
            await agent_node({"messages": []})

        raised = exc_info.value
        # 3 invokes — 1 pre-CLE, 1 post-CLE (raised pairing), 1
        # W2 retry (also raised pairing → reraise).
        assert mock_llm_with_tools.invoke.call_count == 3
        # The reraise is the SECOND post-compaction
        # ``ToolPairingInvalidError`` with the FIRST post-compaction
        # ``ToolPairingInvalidError`` as ``__cause__`` (FIX 1
        # inner-try + FIX 4 chain).
        assert raised is second_exc
        assert raised.__cause__ is first_exc
        # The original ``BadRequestError``s are still attached.
        assert raised.original is original_second
        assert raised.__cause__.original is original_first


# ---------------------------------------------------------------------------
# 🟡 #2 — Classifier→W2 seam test (real BadRequestError → classify_llm_errors → W2 retry)
# ---------------------------------------------------------------------------


class TestClassifierToW2Seam:
    """🟡 #2: REAL BadRequestError end-to-end through ``classify_llm_errors``.

    The round-1 W2 wiring tests raise ``ToolPairingInvalidError``
    directly (signature-string unit matching). The round-2 review
    asked for a pin that drives the REAL BadRequestError through
    ``classify_llm_errors`` (the production wrapper) so the
    classifier's signature detection → ``ToolPairingInvalidError``
    conversion → agent_node W2 catch chain is exercised end-to-end.

    This catches a regression where the classifier stops matching
    the 2013 signature (e.g. signature string drift) — the round-1
    test would still pass (it skips the classifier), but production
    would silently drop the W2 retry.

    The test wraps a raw LLM provider with ``classify_llm_errors``
    (the same wrapping ``build_instance_llms`` does in production
    at the unconditional classifier-wrap block in
    :func:`daemon.graph.build_instance_llms` — see the comment
    block above the wrap, which since the round-2 🟡#3 fix is
    UNCONDITIONAL — the classifier wrap fires regardless of
    ``retry_config`` presence, so the W2 wiring is always active
    in production) and feeds it into the agent_node. The
    provider's first invoke raises a real ``openai.BadRequestError``
    with the canonical 2013 signature; the classifier converts to
    ``ToolPairingInvalidError``; the W2 catch fires; the second
    invoke (with the healed payload) returns a valid ``AIMessage``.
    """

    @pytest.mark.asyncio
    async def test_real_badrequest_2013_drives_w2_through_classifier(
        self, monkeypatch
    ):
        from openai import BadRequestError
        from daemon.graph import classify_llm_errors, create_agent_node

        # Provider: 1st invoke raises a real ``BadRequestError``
        # carrying the canonical 2013 signature. The classifier
        # MUST detect it and raise ``ToolPairingInvalidError`` (the
        # only path into the W2 catch — a raw ``BadRequestError``
        # is NOT caught by the W2 handler).
        class _Provider:
            def __init__(self):
                self.calls: list[list] = []
                self._seq = 0

            def invoke(self, messages):
                self.calls.append(list(messages))
                self._seq += 1
                if self._seq == 1:
                    # Canonical 2013 signature — the classifier
                    # matches it via _matches_pairing_invalid and
                    # raises ToolPairingInvalidError.
                    raise BadRequestError(
                        message=(
                            "openai: invalid params, tool call result "
                            "does not follow tool call (2013)"
                        ),
                        response=MagicMock(),
                        body=None,
                    )
                return AIMessage(
                    content="classifier → W2 → healed success",
                    id="post-classifier-w2",
                )

        raw_provider = _Provider()
        # Wrap the raw provider with the PRODUCTION classifier
        # wrapper. This is the exact transformation
        # ``build_instance_llms`` does at its unconditional
        # classifier-wrap block (see the comment above the wrap in
        # :func:`daemon.graph.build_instance_llms`) — the wrap fires
        # regardless of ``retry_config`` presence.
        wrapped_provider = classify_llm_errors(raw_provider)

        config = {"configurable": {"thread_id": "test-classifier-w2-seam"}}
        agent_node = create_agent_node(
            wrapped_provider,
            system_prompt="You are helpful.",
            compactor=None,
            graph_ref=[None],
            config=config,
            llm_config={"model": "gpt-4o"},
        )

        # The agent_node calls wrapped_provider.invoke, which calls
        # raw_provider.invoke and then validate_llm_response. The
        # 1st raw invoke raises BadRequestError; the classifier
        # catches and raises ToolPairingInvalidError; the W2 catch
        # fires; the 2nd invoke returns the valid AIMessage.
        result = await agent_node({"messages": [HumanMessage(content="hi")]})

        # EXACTLY 2 raw invokes (1st-raise + W2-retry success).
        assert raw_provider._seq == 2, (
            f"Classifier→W2 seam pin: expected EXACTLY 2 raw "
            f"invokes (1st raises BadRequestError → classifier "
            f"converts → W2 catch → 2nd succeeds); got "
            f"{raw_provider._seq}"
        )

        # The response is the 2nd invoke's AIMessage (the post-W2
        # healed response), NOT a propagated BadRequestError
        # (which the classifier should have converted and the W2
        # catch should have handled) and NOT a loud-ERROR fallback.
        assert "messages" in result
        last_ai = None
        for m in result["messages"]:
            if isinstance(m, AIMessage):
                last_ai = m
        assert last_ai is not None
        assert last_ai.content == "classifier → W2 → healed success", (
            f"Classifier→W2 seam: expected post-W2 AIMessage; got "
            f"content={last_ai.content!r}. A non-2013 signature "
            f"drift in the classifier would cause this assertion "
            f"to fail (BadRequestError would propagate past the "
            f"W2 catch because the classifier didn't convert it)."
        )

    @pytest.mark.asyncio
    async def test_non_pairing_badrequest_does_not_drive_w2(
        self, monkeypatch
    ):
        """Belt-and-suspenders: a BadRequestError WITHOUT the 2013
        signature (e.g. a generic 400) is NOT converted to
        ``ToolPairingInvalidError`` by the classifier, so the W2
        catch does NOT fire. The agent_node re-raises the raw
        ``BadRequestError`` (the existing non-retryable path).
        """
        from openai import BadRequestError
        from daemon.graph import classify_llm_errors, create_agent_node

        class _Provider:
            def __init__(self):
                self.calls: list[list] = []
                self._seq = 0

            def invoke(self, messages):
                self.calls.append(list(messages))
                self._seq += 1
                raise BadRequestError(
                    message="missing required field 'messages'",
                    response=MagicMock(),
                    body=None,
                )

        raw_provider = _Provider()
        wrapped_provider = classify_llm_errors(raw_provider)

        config = {"configurable": {"thread_id": "test-classifier-non2013"}}
        agent_node = create_agent_node(
            wrapped_provider,
            system_prompt="You are helpful.",
            compactor=None,
            graph_ref=[None],
            config=config,
            llm_config={"model": "gpt-4o"},
        )

        # The raw BadRequestError is NOT a pairing signature, so
        # the classifier's `raise` line in the except branch
        # re-raises the raw BadRequestError (NOT
        # ToolPairingInvalidError). The W2 catch is specifically
        # for ToolPairingInvalidError, so it does NOT fire — the
        # agent_node re-raises the BadRequestError.
        with pytest.raises(BadRequestError):
            await agent_node({"messages": [HumanMessage(content="hi")]})

        # EXACTLY 1 invoke — the W2 catch never fires (the
        # classifier didn't convert, so the raw BadRequestError
        # propagates).
        assert raw_provider._seq == 1, (
            f"Non-2013 BadRequestError must NOT trigger W2 retry; "
            f"expected 1 invoke, got {raw_provider._seq}"
        )


# ---------------------------------------------------------------------------
# 🟡 #1 — 3rd invoke site pin (graph.py:3134, pre-terminal repair re-invoke)
# ---------------------------------------------------------------------------


class TestPreTerminalThirdInvokeSite:
    """🟡 #1: graph-level pin for the 3rd invoke site.

    The pre-terminal repair path (hallucination-recovery ladder
    Phase 2) fires ONCE after the shipped retry ladder exhausts
    and BEFORE the loud ERROR. The helper
    ``_maybe_pre_terminal_repair`` (graph.py:2863) detects
    truncated (``finish_reason=length``) or
    ``empty_post_ladder`` (``EmptyLLMResponseError``) raises,
    runs the matching preset through ``SymptomRepairEngine``, and
    re-invokes the LLM ONCE at graph.py:3134 (the 3rd invoke
    site) with the surgery-prefixed history.

    Existing pre-terminal tests pin the FAILURE path (3rd invoke
    also raises → second-exception abort → loud ERROR) — see
    ``tests/unit/test_ladder_p2_routed_gap2_second_exception.py``
    and ``test_ladder_p2_routed_gap3_caller_seam_gate.py``. This
    test pins the SUCCESS path: 3rd invoke fires, the post-
    surgery response is the agent_node's emitted message, NOT a
    propagated exception.

    The fixture uses the truncated exception class
    (``LLMResponseValidationError`` with a ``finish_reason=length``
    response) since the empty class has its own exemption
    surface. A ``side_effect`` list drives the LLM:
      - 1st call → raises truncated validation error
      - 2nd call → returns a valid ``AIMessage`` (the post-surgery
        success)

    Asserts: invoke count is EXACTLY 2 at the agent_node level
    (1st raises → 2nd succeeds via the 3rd invoke re-invoke path),
    the response is the 2nd invoke's AIMessage, and the surgery
    doc rides the C2 return.
    """

    @pytest.mark.asyncio
    async def test_third_invoke_succeeds_after_surgery(self, monkeypatch):
        from daemon.config import _reset_symptom_repair_ladder_for_tests
        from daemon.graph import create_agent_node
        from daemon.response_validation import LLMResponseValidationError
        from daemon.services.symptom_repair_engine import SymptomRepairEngine
        from tests.helpers.symptom_repair import ok_summarizer

        # Master ON — the pre-terminal intercept must fire.
        monkeypatch.setenv("ENSEMBLE_SYMPTOM_REPAIR_LADDER", "1")
        _reset_symptom_repair_ladder_for_tests()

        # Engine summarizer stub — the real surgery/budget/doc flow
        # is exercised (the helper reaches the engine before
        # invoking the 3rd time).
        monkeypatch.setattr(
            SymptomRepairEngine,
            "_summarize",
            staticmethod(ok_summarizer),
        )

        # Provider: 1st call raises the truncated validation error
        # (finish_reason=length), 2nd call returns a valid AIMessage.
        class _Provider:
            def __init__(self):
                self.calls: list[list] = []
                self._seq = 0

            def invoke(self, messages):
                self.calls.append(list(messages))
                self._seq += 1
                if self._seq == 1:
                    raise LLMResponseValidationError(
                        "truncated response",
                        response=AIMessage(
                            content="partial answer that hit the token limit",
                            response_metadata={"finish_reason": "length"},
                            id="trunc-1",
                        ),
                    )
                return AIMessage(
                    content="post-surgery healed",
                    id="post-2",
                )

        provider = _Provider()
        config = {"configurable": {"thread_id": "test-3rd-invoke"}}
        agent_node = create_agent_node(
            provider,
            system_prompt="You are helpful.",
            compactor=None,
            graph_ref=[None],
            config=config,
            llm_config={"model": "gpt-4o"},
        )

        result = await agent_node({"messages": [HumanMessage(content="hi")]})

        # EXACTLY 2 invokes at the agent_node level — the 1st raised
        # truncated, the pre-terminal intercept fired, the surgery
        # completed, and the 3rd invoke (graph.py:3134) returned the
        # post-surgery success. NO third invoke (the success path
        # terminates here, not the failure path).
        assert provider._seq == 2, (
            f"3rd-invoke-site pin: expected EXACTLY 2 invokes "
            f"(1st-raise + 3rd-re-invoke success); got {provider._seq}"
        )

        # The emitted message is the 2nd invoke's AIMessage (the
        # post-surgery healed response), NOT a propagated exception
        # and NOT a loud-ERROR fallback.
        assert "messages" in result
        assert len(result["messages"]) >= 1
        # The last emitted AIMessage is the post-surgery success.
        last_ai_message = None
        for m in result["messages"]:
            if isinstance(m, AIMessage):
                last_ai_message = m
        assert last_ai_message is not None, (
            f"3rd-invoke success: no AIMessage in result; got "
            f"{[type(m).__name__ for m in result['messages']]}"
        )
        assert last_ai_message.content == "post-surgery healed", (
            f"3rd-invoke success: expected post-surgery AIMessage; "
            f"got content={last_ai_message.content!r}"
        )


# ---------------------------------------------------------------------------
# ROUND-2 — W2 INVALID-SHAPE POISON (invalid_tool_calls first-class)
# ---------------------------------------------------------------------------


class TestW2InvalidShapeHeal:
    """ROUND-2 — W2 wiring pin for the invalid_tool_calls poison shape.

    The round-2 live bug (incident 03d7657f task 10816, 2026-10-09):
    an ``AIMessage(tool_calls=[], invalid_tool_calls=[X])`` was
    committed mid-history. The W1 heal ran but the round-1 ownership
    tracking set missed X (X lived only in ``invalid_tool_calls``),
    so the in-block ``ToolMessage(X)`` was flagged
    block-ownership-orphan and STRIPPED. The next dispatch re-shipped
    the now-unanswered invalid call, the strict gateway 2013-rejected,
    and W2 caught it but the heal AGAIN stripped the orphan TM
    (perpetual loop, the failure arc never resolved).

    Round-2 fix (this branch): the union lands in the ownership
    tracking set so the TM answering an invalid-only AIMessage is
    block-ownership-valid; the strip is dead; W2's single retry
    succeeds.

    This test pins the round-2 wiring at the agent_node W2 site:
    the FIRST invoke carries the invalid-shape history; the W1
    probe flags a violation (well-formed call X is missing from
    the adjacent block); the W1 heal synthesizes a partner for X;
    the FIRST invoke runs against the healed history and returns
    a valid response (the gateway sees the now-paired
    AIMessage(tc=[X]) + TM(X) and accepts).

    The point is the LLM dispatch NEVER raises 2013 against the
    healed payload (the W2 retry path is unnecessary in the
    round-2 fix — the W1 probe + heal alone resolves the
    invalid-shape adjacency). The negative pin asserts the
    round-1 failure arc is dead: probe CLEAN, heal no-op, NO
    invocation count burn, NO retry, the response rides the
    first invoke.
    """

    @pytest.mark.asyncio
    async def test_w1_heal_alone_resolves_invalid_shape_no_w2_retry(
        self, mock_llm_with_tools
    ):
        """The round-1 failure arc is dead.

        History: ``AIMessage(tool_calls=[], invalid_tool_calls=[X])``
        + ``ToolMessage(X)`` + ``HumanMessage``. The W1 probe runs
        against ``full_messages`` (system + state). The probe is
        CLEAN (round-2 union) — no violation to flag. The W1 heal
        is a no-op. The first invoke runs against the as-supplied
        payload. NO 2013 fires. NO W2 retry needed. Invoke count
        is EXACTLY 1.
        """
        from daemon.graph import create_agent_node

        # State messages: an AIMessage carrying only an invalid call
        # (the live-bug repair shape) followed by the answering TM
        # (the DB-repair-lane TM the round-1 heal used to strip).
        # The round-2 union keeps this TM (block-ownership-valid).
        invalid_call_id = "call_8ed9e1771dca42348dfa7ca0"
        ai_invalid = AIMessage(
            content="",
            invalid_tool_calls=[
                {
                    "id": invalid_call_id,
                    "name": "do_thing",
                    "args": "{broken",
                    "type": "invalid_tool_call",
                    "error": "Failed to parse tool call",
                }
            ],
        )
        # DB-repair-lane TM: plain uuid id (NOT a partner-synth
        # prefix), the exact W-C verbatim shape.
        tm_uuid = "1c2a9d4f-3b71-4f0e-9a23-deadbeef0001"
        tm_answer = ToolMessage(
            content="[repaired result]",
            tool_call_id=invalid_call_id,
            name="do_thing",
            id=tm_uuid,
        )
        state_messages = [
            ai_invalid,
            tm_answer,
            HumanMessage(content="continue"),
        ]

        # First invoke returns a valid AIMessage. If the round-1
        # strip recurred, the payload would be missing the TM,
        # the gateway would 2013, the W2 retry would fire — but
        # we only put ONE side_effect, so any second consume
        # would raise StopIteration and the test would fail with
        # the round-1 bug signature.
        first_response = AIMessage(content="OK after W1 heal")
        mock_llm_with_tools.invoke.side_effect = [first_response]

        config = {"configurable": {"thread_id": "test-w2-invalid-shape"}}
        agent_node = create_agent_node(
            mock_llm_with_tools,
            system_prompt="You are helpful.",
            compactor=None,
            graph_ref=[None],
            config=config,
            llm_config={"model": "gpt-4o"},
        )

        result = await agent_node({"messages": state_messages})

        # EXACTLY 1 invoke — the W1 probe is CLEAN (round-2
        # union kept the TM), no violation to heal, no W2
        # retry. If the round-1 bug recurred, the W2 retry
        # would have fired and the second consume of
        # side_effect would raise StopIteration.
        assert mock_llm_with_tools.invoke.call_count == 1, (
            f"round-2 W2 pin: expected EXACTLY 1 invoke (W1 "
            f"heal resolved the invalid-shape adjacency); "
            f"got {mock_llm_with_tools.invoke.call_count}. "
            f"Round-1 failure arc recurred — the union at the "
            f"ownership-tracking refresh site is broken."
        )

        # The first invoke's payload is healed/order-valid
        # (it's the as-supplied history — round-2 keeps it
        # intact because the union made the TM
        # block-ownership-valid).
        from daemon.tool_pairing_history import has_pairing_violations
        first_payload = mock_llm_with_tools.invoke.call_args_list[0].args[0]
        assert has_pairing_violations(first_payload) is False, (
            f"first-invoke payload must be healed/order-valid "
            f"after W1 probe + heal; got {first_payload}"
        )

        # The TM is in the first payload (round-2 didn't
        # strip it — the round-1 regression is dead).
        assert tm_answer in first_payload, (
            f"round-2 negative pin: the DB-repair-lane TM "
            f"must remain in the LLM-bound payload; round-1 "
            f"used to strip it. If this assertion fires, the "
            f"union at the ownership-tracking refresh site is "
            f"broken."
        )
        assert first_payload[first_payload.index(tm_answer)].id == tm_uuid

        # The response rides the first invoke's result.
        assert "messages" in result
        assert len(result["messages"]) == 1
        assert result["messages"][0].content == "OK after W1 heal"

    @pytest.mark.asyncio
    async def test_w2_blind_retry_leaves_invalid_shape_payload_intact(
        self, monkeypatch
    ):
        """W2 catch's unioned-validator no-op path (round-2 follow-up).

        PINS the literal commissioned test-minimum arc "2013 → heal
        → retry passes" at the W2 call site with the invalid-shape
        repair payload. The W1-alone pin above + the payload-agnostic
        classifier→W2 seam test cover the two pieces separately;
        this test ties them together end-to-end:

            1. State carries the W-C live repair shape — an
               ``AIMessage(tool_calls=[], invalid_tool_calls=[X])``
               followed by a plain-uuid-id ``ToolMessage(X)``
               (DB-repair-lane TM the round-1 heal stripped).
               The W1 probe is CLEAN post-union (no violation to
               flag, no heal needed).
            2. The provider is a BLIND-rejection gateway — it
               raises the canonical 2013 signature REGARDLESS of
               payload content. The first invoke carries the
               probe-clean as-supplied history and 2013-rejects.
            3. The classifier (production ``classify_llm_errors``
               wrap, same as ``build_instance_llms`` uses in
               production) detects the 2013 signature and raises
               ``ToolPairingInvalidError``.
            4. The agent_node W2 catch fires, runs the
               full-history validator+healer on the LLM-bound
               payload, sees probe-clean (round-2 union), and
               performs a no-op heal — the ToolMessage(X)
               survives by IDENTITY in the retry payload.
            5. The second invoke runs against the intact payload
               and returns a valid AIMessage.

        This guards against a future W2-catch regression that
        strips unowned-looking TMs during a blind retry (the
        round-1 failure mode, but at the W2 catch site — the
        heal-once-retry path would have stripped the TM, the
        retry would carry the unanswered invalid call, the
        gateway would 2013 again, and the W2 path would have
        no second retry to catch it). Asserting identity
        preservation across invokes pins the W2 heal against
        any future drift back to the round-1 ownership-tracking
        miss.
        """
        from openai import BadRequestError
        from daemon.graph import classify_llm_errors, create_agent_node
        from daemon.tool_pairing_history import has_pairing_violations

        # State messages — the W-C live repair shape, mid-history
        # (no leading HumanMessage at index 0; the agent_node
        # prepends the system prompt and the state carries only
        # the conversation turn).
        invalid_call_id = "call_8ed9e1771dca42348dfa7ca0"
        ai_invalid = AIMessage(
            content="",
            invalid_tool_calls=[
                {
                    "id": invalid_call_id,
                    "name": "do_thing",
                    "args": "{broken",
                    "type": "invalid_tool_call",
                    "error": "Failed to parse tool call",
                }
            ],
        )
        tm_uuid = "1c2a9d4f-3b71-4f0e-9a23-deadbeef0001"
        tm_answer = ToolMessage(
            content="[repaired result]",
            tool_call_id=invalid_call_id,
            name="do_thing",
            id=tm_uuid,
        )
        state_messages = [
            ai_invalid,
            tm_answer,
            HumanMessage(content="continue"),
        ]

        # Provider: blind-rejection gateway — raises the
        # canonical 2013 signature REGARDLESS of payload
        # content (simulates a stricter gateway that doesn't
        # introspect the heal). The classifier MUST detect
        # the 2013 signature and convert to
        # ``ToolPairingInvalidError``; otherwise the W2 catch
        # doesn't fire.
        class _Provider:
            def __init__(self):
                self.calls: list[list] = []
                self._seq = 0

            def invoke(self, messages):
                self.calls.append(list(messages))
                self._seq += 1
                if self._seq == 1:
                    raise BadRequestError(
                        message=(
                            "openai: invalid params, tool call result "
                            "does not follow tool call (2013)"
                        ),
                        response=MagicMock(),
                        body=None,
                    )
                return AIMessage(
                    content="classifier → W2 → no-op heal → retry success",
                    id="post-w2-retry",
                )

        raw_provider = _Provider()
        # Wrap with the PRODUCTION ``classify_llm_errors``
        # wrapper (same wrap ``build_instance_llms`` uses in
        # production at the unconditional classifier-wrap
        # block in :func:`daemon.graph.build_instance_llms`).
        wrapped_provider = classify_llm_errors(raw_provider)

        config = {
            "configurable": {
                "thread_id": "test-w2-blind-retry-invalid-shape",
            }
        }
        agent_node = create_agent_node(
            wrapped_provider,
            system_prompt="You are helpful.",
            compactor=None,
            graph_ref=[None],
            config=config,
            llm_config={"model": "gpt-4o"},
        )

        result = await agent_node({"messages": state_messages})

        # EXACTLY 2 raw invokes — 1st raised 2013 (classifier
        # converted → W2 catch fired → no-op heal → 2nd
        # succeeded). A 3rd invoke would mean the W2 retry
        # burned out without resolving the shape.
        assert raw_provider._seq == 2, (
            f"W2 blind-retry pin: expected EXACTLY 2 raw "
            f"invokes (1st 2013 → classifier → W2 catch → "
            f"no-op heal → 2nd success); got "
            f"{raw_provider._seq}"
        )

        # Both invoke payloads must be probe-clean (round-2
        # union landed; no violation to flag in either
        # attempt). The 1st-invoke probe-clean is the
        # as-supplied state (post-union); the 2nd-invoke
        # probe-clean is the no-op heal output (same content
        # — heal didn't mutate).
        first_payload = raw_provider.calls[0]
        second_payload = raw_provider.calls[1]
        assert has_pairing_violations(first_payload) is False, (
            f"1st-invoke payload must be probe-clean "
            f"post-union; got violations in {first_payload}"
        )
        assert has_pairing_violations(second_payload) is False, (
            f"2nd-invoke (W2 retry) payload must remain "
            f"probe-clean after the no-op heal; got "
            f"violations in {second_payload}"
        )

        # THE ROUND-2 NEGATIVE PIN — ToolMessage(X) survives
        # by IDENTITY in the retry payload. The W2 catch's
        # unioned-validator no-op path is the contract; a
        # future regression that re-introduces the round-1
        # strip at the W2 call site would fail this
        # assertion (the TM would be missing from the retry
        # payload, and the strict gateway would 2013 again
        # on the unanswered invalid call).
        assert tm_answer in second_payload, (
            f"W2 blind-retry negative pin: the DB-repair-"
            f"lane TM must survive by identity in the retry "
            f"payload; the round-1 strip recurred at the W2 "
            f"catch site if this assertion fires. Retry "
            f"payload: {second_payload}"
        )
        # Identity-preserved across the W2 heal — same object
        # reference, same uuid id. The heal ran validate-
        # and-heal-messages but the probe was clean so the
        # list was returned unchanged.
        assert second_payload[second_payload.index(tm_answer)] is tm_answer, (
            f"W2 retry payload must preserve the TM by "
            f"identity (no-op heal — the probe was clean, "
            f"nothing to strip or synthesize); got a "
            f"different object at the same index."
        )
        assert second_payload[second_payload.index(tm_answer)].id == tm_uuid

        # The 1st and 2nd payloads carry the SAME TM list
        # contents (the heal is a no-op for probe-clean
        # inputs — the list is returned with the same
        # elements in the same order).
        assert (
            [id(m) for m in first_payload]
            == [id(m) for m in second_payload]
        ), (
            f"W2 heal is a no-op for probe-clean inputs; "
            f"the retry payload must carry the same object "
            f"identities as the 1st payload. 1st: "
            f"{first_payload}, 2nd: {second_payload}"
        )

        # The response rides the 2nd invoke's AIMessage —
        # NOT a propagated BadRequestError (the classifier
        # would have converted; a non-2013 signature drift
        # would surface here as a BadRequestError instead).
        assert "messages" in result
        last_ai = None
        for m in result["messages"]:
            if isinstance(m, AIMessage):
                last_ai = m
        assert last_ai is not None, (
            f"W2 retry success must surface the 2nd-invoke "
            f"AIMessage in the agent_node return; got "
            f"{[type(m).__name__ for m in result['messages']]}"
        )
        assert (
            last_ai.content
            == "classifier → W2 → no-op heal → retry success"
        ), (
            f"W2 retry pin: expected the 2nd-invoke's "
            f"AIMessage content; got content="
            f"{last_ai.content!r}. A classifier signature-"
            f"drift or a non-W2 routing would surface as a "
            f"propagated exception instead."
        )
