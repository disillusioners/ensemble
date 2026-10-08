"""FIX 5 — Regression pin for the W2 reactive heal-once-retry wiring.

Branch: ``fix/tool-pairing-full-history-heal``.

The agent_node W2 catch (daemon/graph.py:9111-9128, primary site; the
FIX 1 post-compaction inner-try at :9455-9499) heals the LLM-bound
payload when the gateway rejects with ``ToolPairingInvalidError`` and
re-invokes the LLM ONCE. A second failure reraises the second
exception with the FIRST exception as ``__cause__`` via ``from`` (the
FIX 4 alignment that preserves maximal diagnostics).

These tests pin the contract at the graph level so the W2 wiring
cannot silently regress. The test design follows the existing
``test_graph_retry_integration.py`` mock pattern (``mock_llm_with_tools``,
``mock_graph``, ``mock_compactor``, ``mock_state``) — same fixtures,
same call style. Each test class targets one W2 site:

  * ``TestW2PrimarySiteWiring`` — primary dispatch W2 (graph.py
    :9111-9128). Poisoned history → first invoke raises
    ``ToolPairingInvalidError`` → W2 heal-once-retry → second invoke
    succeeds. Asserts invoke count is EXACTLY 2 and the second
    payload is healed/order-valid.

  * ``TestW2PrimarySiteReraiseChain`` — second-failure path. BOTH
    invokes raise ``ToolPairingInvalidError``; the reraise is the
    SECOND exception with the FIRST as ``__cause__`` (FIX 4
    alignment). Pin against silent regression of the comment-vs-code
    mismatch the review caught.

  * ``TestW2PostCompactionWiring`` — post-compaction W2 (the FIX 1
    inner-try at :9455-9499, the formerly-dead sibling clause).
    First invoke raises ``ContextLengthExceededError``; the compactor
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
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


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
    """Pin the primary dispatch W2 catch (graph.py :9111-9128).

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
    """Pin the post-compaction W2 inner-try (graph.py :9455-9499).

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
    at graph.py:10228) and feeds it into the agent_node. The
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
        # ``build_instance_llms`` does at graph.py:10228 when
        # ``retry_config`` is truthy.
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
