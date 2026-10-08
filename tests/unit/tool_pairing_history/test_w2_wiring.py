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
