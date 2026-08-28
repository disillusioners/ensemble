"""Tests for message serialization utilities.

Tests the functions in daemon/utils.py:
- serialize_message(): Serializes LangChain messages to dict format
- _stable_message_id(): Generates deterministic IDs for messages without .id
- parse_think_tags(): Extracts <think/> tags from content
- get_next_sequence(): Monotonic sequence counter for checkpoint events
"""

import pytest
from unittest.mock import MagicMock

from langchain_core.messages import (
    HumanMessage,
    AIMessage,
    ToolMessage,
    SystemMessage,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_event_repo():
    """Create a mock EventRepository."""
    repo = MagicMock()
    repo.create_event = MagicMock(return_value=MagicMock(id=1))
    return repo


@pytest.fixture
def instance_id():
    """Sample instance ID."""
    return "test-instance-123"


@pytest.fixture
def messages():
    """Sample message list for checkpoint events."""
    return [
        {"message_id": "msg-1", "role": "user", "content": "Hello"},
        {"message_id": "msg-2", "role": "assistant", "content": "Hi there!"},
    ]


# ============================================================================
# Test serialize_message() - All Message Types
# ============================================================================


class TestSerializeMessageAllTypes:
    """Tests for serialize_message() with all message types."""

    def test_human_message_serialization(self):
        """HumanMessage is serialized with role='user'."""
        from daemon.utils import serialize_message

        msg = HumanMessage(content="Hello, world!")
        result = serialize_message(msg)

        assert result["role"] == "user"
        assert result["content"] == "Hello, world!"
        assert result["message_id"] is not None
        assert result["thinking"] is None
        assert result["tool_calls"] is None

    def test_ai_message_serialization(self):
        """AIMessage is serialized with role='assistant'."""
        from daemon.utils import serialize_message

        msg = AIMessage(content="I'm an AI assistant.")
        result = serialize_message(msg)

        assert result["role"] == "assistant"
        assert result["content"] == "I'm an AI assistant."
        assert result["message_id"] is not None
        assert result["thinking"] is None

    def test_tool_message_serialization(self):
        """ToolMessage is serialized with role='tool'."""
        from daemon.utils import serialize_message

        msg = ToolMessage(
            content="Tool output result",
            tool_call_id="call_abc123",
            name="bash",
        )
        result = serialize_message(msg)

        assert result["role"] == "tool"
        assert result["content"] == "Tool output result"
        assert result["message_id"] is not None
        assert "tool_call_id" not in result  # Not in output dict

    def test_system_message_serialization(self):
        """SystemMessage is serialized with role='system'."""
        from daemon.utils import serialize_message

        msg = SystemMessage(content="You are a helpful assistant.")
        result = serialize_message(msg)

        assert result["role"] == "system"
        assert result["content"] == "You are a helpful assistant."
        assert result["message_id"] is not None


# ============================================================================
# Test serialize_message() - Thinking Extraction (5 Paths)
# ============================================================================


class TestSerializeMessageThinkingExtraction:
    """Tests for serialize_message() thinking extraction from 5 possible paths."""

    def test_thinking_extraction_path1_additional_kwargs_reasoning_content(self):
        """Path 1: additional_kwargs.get('reasoning_content')."""
        from daemon.utils import serialize_message

        msg = AIMessage(
            content="The answer is 42.",
            additional_kwargs={"reasoning_content": "Let me calculate..."},
        )
        result = serialize_message(msg)

        assert result["thinking"] == "Let me calculate..."

    def test_thinking_extraction_path2_additional_kwargs_thinking(self):
        """Path 2: additional_kwargs.get('thinking')."""
        from daemon.utils import serialize_message

        msg = AIMessage(
            content="Done.",
            additional_kwargs={"thinking": "Internal monologue..."},
        )
        result = serialize_message(msg)

        assert result["thinking"] == "Internal monologue..."

    def test_thinking_extraction_path3_reasoning_content_attribute(self):
        """Path 3: msg.reasoning_content attribute (some Claude models)."""
        from daemon.utils import serialize_message

        msg = AIMessage(content="Final answer.")
        msg.reasoning_content = "Step-by-step reasoning..."

        result = serialize_message(msg)

        assert result["thinking"] == "Step-by-step reasoning..."

    def test_thinking_extraction_path4_thinking_attribute(self):
        """Path 4: msg.thinking attribute (Claude models)."""
        from daemon.utils import serialize_message

        msg = AIMessage(content="Response.")
        msg.thinking = "Claude's thought process..."

        result = serialize_message(msg)

        assert result["thinking"] == "Claude's thought process..."

    def test_thinking_extraction_path5_content_list_reasoning_block(self):
        """Path 5: msg.content as list with type='reasoning' blocks."""
        from daemon.utils import serialize_message

        msg = AIMessage(
            content=[
                {"type": "text", "text": "The answer is 42."},
                {"type": "reasoning", "reasoning": "Working through the problem..."},
            ]
        )
        result = serialize_message(msg)

        assert result["thinking"] == "Working through the problem..."

    def test_thinking_extraction_path5_with_summary_text(self):
        """Path 5 variant: type='reasoning' with summary_text instead of reasoning."""
        from daemon.utils import serialize_message

        msg = AIMessage(
            content=[
                {"type": "text", "text": "Answer here."},
                {"type": "reasoning", "summary_text": "Brief summary of thinking..."},
            ]
        )
        result = serialize_message(msg)

        assert result["thinking"] == "Brief summary of thinking..."

    def test_thinking_extraction_priority_order(self):
        """Earlier paths take precedence when multiple thinking sources exist."""
        from daemon.utils import serialize_message

        # Path 1 should take precedence over Path 3
        msg = AIMessage(
            content="Answer.",
            additional_kwargs={"reasoning_content": "From additional_kwargs"},
        )
        msg.reasoning_content = "From attribute"

        result = serialize_message(msg)

        assert result["thinking"] == "From additional_kwargs"

    def test_no_thinking_when_not_present(self):
        """Thinking is None when no thinking source exists."""
        from daemon.utils import serialize_message

        msg = AIMessage(content="Plain response.")
        result = serialize_message(msg)

        assert result["thinking"] is None


# ============================================================================
# Test serialize_message() with msg.id = None (Fallback)
# ============================================================================


class TestSerializeMessageWithNoneId:
    """Tests for serialize_message() when msg.id is None."""

    def test_uuid_generated_when_msg_id_is_none(self):
        """UUID fallback is generated when msg.id is None."""
        from daemon.utils import serialize_message

        msg = HumanMessage(content="Test message")
        msg.id = None

        result = serialize_message(msg)

        assert result["message_id"] is not None
        # Should be a valid UUID string
        import uuid
        uuid.UUID(result["message_id"])  # Raises ValueError if not a valid UUID

    def test_none_id_gets_unique_ids(self):
        """Each call with msg.id=None gets a unique UUID."""
        from daemon.utils import serialize_message

        msg1 = HumanMessage(content="Same content")
        msg1.id = None
        msg2 = HumanMessage(content="Same content")
        msg2.id = None

        result1 = serialize_message(msg1)
        result2 = serialize_message(msg2)

        # Each serialization generates a new UUID
        assert result1["message_id"] != result2["message_id"]

    def test_different_roles_get_unique_ids(self):
        """Different roles with no id get unique UUIDs."""
        from daemon.utils import serialize_message

        msg1 = HumanMessage(content="Content A")
        msg1.id = None
        msg2 = AIMessage(content="Content A")
        msg2.id = None

        result1 = serialize_message(msg1)
        result2 = serialize_message(msg2)

        assert result1["message_id"] != result2["message_id"]


# ============================================================================
# Test message_id with real msg.id takes priority over UUID fallback
# ============================================================================


class TestMessageIdPriority:
    """Tests that msg.id is used when available, UUID only as fallback."""

    def test_real_id_takes_priority(self):
        """When msg.id is set, it is used as message_id."""
        from daemon.utils import serialize_message

        msg = HumanMessage(content="Test")
        msg.id = "real-msg-id-123"

        result = serialize_message(msg)

        assert result["message_id"] == "real-msg-id-123"

    def test_none_id_triggers_uuid(self):
        """When msg.id is None, a UUID is generated."""
        from daemon.utils import serialize_message

        msg = HumanMessage(content="Test")
        msg.id = None

        result = serialize_message(msg)

        import uuid
        uuid.UUID(result["message_id"])  # Validates it's a UUID


# ============================================================================
# Test parse_think_tags()
# ============================================================================


class TestParseThinkTags:
    """Tests for parse_think_tags() function."""

    def test_extracts_think_tags(self):
        """Think tags are extracted and removed from content."""
        from daemon.utils import parse_think_tags

        content = "Answer here. <think>Let me think about this...</think> More text."
        cleaned, thinking = parse_think_tags(content)

        assert thinking == "Let me think about this..."
        assert "Let me think" not in cleaned
        assert "Answer here." in cleaned
        assert "More text." in cleaned

    def test_no_think_tags_returns_original(self):
        """Content without think tags returns original and None."""
        from daemon.utils import parse_think_tags

        content = "Plain content without tags."
        cleaned, thinking = parse_think_tags(content)

        assert thinking is None
        assert cleaned == content

    def test_multiple_think_tags_combined(self):
        """Multiple think blocks are combined with newlines."""
        from daemon.utils import parse_think_tags

        content = (
            "Answer. <think>First thought...</think> "
            "More. <think>Second thought...</think> Done."
        )
        cleaned, thinking = parse_think_tags(content)

        assert "First thought..." in thinking
        assert "Second thought..." in thinking
        assert "Answer." in cleaned

    def test_think_tags_case_insensitive(self):
        """Think tags match case-insensitively."""
        from daemon.utils import parse_think_tags

        content = "<THINK>UPPER CASE</THINK> Answer"
        cleaned, thinking = parse_think_tags(content)

        assert thinking == "UPPER CASE"

    def test_think_tags_with_attributes(self):
        """Think tags with attributes are handled."""
        from daemon.utils import parse_think_tags

        content = "<think model='claude'>Thinking...</think> Answer"
        cleaned, thinking = parse_think_tags(content)

        assert thinking == "Thinking..."
        assert "Answer" in cleaned

    def test_think_tags_with_newlines(self):
        """Think content with newlines is preserved."""
        from daemon.utils import parse_think_tags

        content = "<think>Line 1\nLine 2\nLine 3</think> Answer"
        cleaned, thinking = parse_think_tags(content)

        assert "Line 1" in thinking
        assert "Line 2" in thinking
        assert "\n" in thinking


# ============================================================================
# Test serialize_message() - Think Tag Extraction Integration
# ============================================================================


class TestSerializeMessageThinkTagIntegration:
    """Tests for think tag extraction via serialize_message()."""

    def test_think_tags_extracted_from_content(self):
        """Think tags in content are extracted to thinking_extracted."""
        from daemon.utils import serialize_message

        msg = AIMessage(
            content="<think>Internal reasoning...</think> Here is my answer."
        )
        result = serialize_message(msg)

        assert result["thinking_extracted"] == "Internal reasoning..."
        assert "Internal reasoning" not in result["content"]
        assert "Here is my answer." in result["content"]

    def test_both_thinking_sources_and_think_tags(self):
        """Both additional_kwargs thinking and think tags are captured."""
        from daemon.utils import serialize_message

        msg = AIMessage(
            content="<think>Extracted from tags</think> Answer.",
            additional_kwargs={"thinking": "From additional_kwargs"},
        )
        result = serialize_message(msg)

        # additional_kwargs takes precedence
        assert result["thinking"] == "From additional_kwargs"
        # But think tags are also extracted
        assert result["thinking_extracted"] == "Extracted from tags"


# ============================================================================
# Test serialize_message() - Tool Calls
# ============================================================================


class TestSerializeMessageToolCalls:
    """Tests for serialize_message() tool call handling."""

    def test_tool_calls_as_dict(self):
        """Tool calls in dict format are serialized."""
        from daemon.utils import serialize_message

        msg = AIMessage(
            content="",
            tool_calls=[
                {"id": "call_1", "name": "bash", "args": {"command": "ls"}},
            ],
        )
        result = serialize_message(msg)

        assert result["tool_calls"] is not None
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["id"] == "call_1"
        assert result["tool_calls"][0]["name"] == "bash"

    def test_tool_calls_with_tool_outputs(self):
        """Tool outputs are injected into serialized tool calls."""
        from daemon.utils import serialize_message

        msg = AIMessage(
            content="Result",
            tool_calls=[
                {"id": "call_1", "name": "bash", "args": {}},
            ],
        )
        tool_outputs = {"call_1": "file1.txt\nfile2.txt"}
        result = serialize_message(msg, tool_outputs=tool_outputs)

        assert result["tool_calls"][0]["output"] == "file1.txt\nfile2.txt"

    def test_no_tool_calls_when_absent(self):
        """tool_calls is None when message has no tool calls."""
        from daemon.utils import serialize_message

        msg = AIMessage(content="Plain response.")
        result = serialize_message(msg)

        assert result["tool_calls"] is None


# ============================================================================
# Test serialize_message() — W1 marker surfacing
#
# Verifies the additive wiring added by the W1 INTERIM RESOLUTION batch
# (this commit). ``serialize_message`` now surfaces ``injected_message``,
# ``context_kind``, and ``source`` from ``additional_kwargs`` as ADDITIVE
# top-level keys. Plain messages that never carried the markers keep the
# legacy dict shape (no spurious keys).
# ============================================================================


class TestSerializeMessageW1MarkerSurfacing:
    """W1 batch — additive surfacing of durable markers.

    Contract (see ``daemon/utils.py:181-209``):
      * ``injected_message`` — surfaced when present in
        ``additional_kwargs`` (any truthy/falsy value preserved verbatim).
      * ``context_kind`` — surfaced when non-empty
        ``additional_kwargs["context_kind"]``.
      * ``source`` — surfaced when non-empty
        ``additional_kwargs["source"]``.
      * Plain messages that never tagged ``additional_kwargs`` keep the
        legacy dict shape — NO spurious keys.

    The contract is additive — existing API consumers continue to see the
    same messages they always have. New keys appear ONLY when the source
    LangChain message carried the marker at construction time.
    """

    def test_plain_human_message_has_no_marker_keys(self):
        """A vanilla ``HumanMessage`` MUST NOT acquire spurious
        ``injected_message`` / ``context_kind`` / ``source`` keys —
        additive contract preserves the legacy shape for messages that
        were never tagged as injected.
        """
        from daemon.utils import serialize_message

        msg = HumanMessage(content="just a normal turn")
        result = serialize_message(msg)

        assert "injected_message" not in result
        assert "context_kind" not in result
        assert "source" not in result
        # Legacy keys still present.
        assert result["role"] == "user"
        assert result["content"] == "just a normal turn"

    def test_context_message_surfaces_context_kind_only(self):
        """A context builder's HumanMessage (with both markers set)
        surfaces ONLY the keys that are non-empty — same back-compat
        pattern as the pre-W1 ``context_kind`` surfacing at
        ``daemon/utils.py:181-189`` (Phase 4 CHANGE 1).
        """
        from daemon.utils import serialize_message

        msg = HumanMessage(
            content="[SYSTEM CONTEXT: Skills]\n\nbody",
            additional_kwargs={
                "injected_message": True,
                "context_kind": "skills",
            },
        )
        result = serialize_message(msg)

        # Both structured markers flow through.
        assert result["injected_message"] is True
        assert result["context_kind"] == "skills"
        # No source on a vanilla context message.
        assert "source" not in result

    def test_injected_message_marker_surfaced_alone(self):
        """The user-injection FIFO drain (``daemon/graph.py:2894``)
        stamps ``{"injected_message": True}`` when no ``source`` is
        present on the FIFO entry. The serialized output mirrors this
        exactly — ``injected_message=True`` and NO ``source`` key
        (additive contract: the key is omitted, not nulled).
        """
        from daemon.utils import serialize_message

        msg = HumanMessage(
            content="injected user msg",
            additional_kwargs={"injected_message": True},
        )
        result = serialize_message(msg)

        assert result["injected_message"] is True
        assert "context_kind" not in result
        assert "source" not in result

    def test_source_marker_surfaced_with_injected_message(self):
        """The user-injection FIFO drain with a non-null ``source``
        stamps ``{"injected_message": True, "source": "..."}``. The
        serialized output surfaces BOTH keys — GET /messages can now
        render the originating caller's provenance.
        """
        from daemon.utils import serialize_message

        msg = HumanMessage(
            content="injected from internal agent",
            additional_kwargs={
                "injected_message": True,
                "source": "internal_agent:abc123",
            },
        )
        result = serialize_message(msg)

        assert result["injected_message"] is True
        assert result["source"] == "internal_agent:abc123"
        assert "context_kind" not in result

    def test_internal_report_source_round_trips(self):
        """The report-injection drain (W1 batch at
        ``daemon/graph.py:3080-3092``) stamps
        ``source="internal_report:<child_iid>"`` alongside
        ``injected_message``. The serialized output surfaces both.
        """
        from daemon.utils import serialize_message

        msg = HumanMessage(
            content="child report body",
            additional_kwargs={
                "injected_message": True,
                "source": "internal_report:child-instance-xyz",
            },
        )
        result = serialize_message(msg)

        assert result["injected_message"] is True
        assert result["source"] == "internal_report:child-instance-xyz"

    def test_source_without_injected_message_is_surfaced(self):
        """A message that carries ``source`` but NOT ``injected_message``
        (e.g. a future user-API first-arrival enrichment) still gets
        ``source`` surfaced. The two keys are independent additive
        channels.
        """
        from daemon.utils import serialize_message

        msg = HumanMessage(
            content="enriched first-arrival",
            additional_kwargs={"source": "telegram:user:42"},
        )
        result = serialize_message(msg)

        assert result["source"] == "telegram:user:42"
        assert "injected_message" not in result
        assert "context_kind" not in result

    def test_empty_string_marker_values_are_omitted(self):
        """Empty-string ``context_kind`` / ``source`` are treated as
        absent — additive contract skips falsy values to avoid
        meaningless empty-string keys in the wire payload.
        """
        from daemon.utils import serialize_message

        msg = HumanMessage(
            content="empty markers",
            additional_kwargs={
                "injected_message": True,
                "context_kind": "",
                "source": "",
            },
        )
        result = serialize_message(msg)

        assert result["injected_message"] is True
        assert "context_kind" not in result
        assert "source" not in result

    def test_no_additional_kwargs_attribute_omits_all(self):
        """Defensive: a message object without an ``additional_kwargs``
        attribute (custom BaseMessage subclass) gets no marker keys —
        ``getattr(msg, 'additional_kwargs', None) or {}`` short-circuits
        cleanly without AttributeError.
        """
        from daemon.utils import serialize_message

        # Build a bare object that mimics LangChain BaseMessage without
        # the additional_kwargs attribute.
        class BareMessage:
            type = "user"
            id = "bare-1"
            content = "bare content"
            tool_calls = []

        result = serialize_message(BareMessage())

        assert result["role"] == "user"
        assert result["content"] == "bare content"
        assert "injected_message" not in result
        assert "context_kind" not in result
        assert "source" not in result


# ============================================================================
# Test get_next_sequence()
# ============================================================================


class TestGetNextSequence:
    """Tests for get_next_sequence() monotonic counter."""

    def test_sequence_starts_at_one(self):
        """First call returns 1 for a new instance."""
        from daemon.utils import get_next_sequence, _sequence_counter

        # Clear any existing state
        _sequence_counter.clear()

        result = get_next_sequence("test-instance")

        assert result == 1

    def test_sequence_increments_monotonically(self):
        """Each call returns the next sequential number."""
        from daemon.utils import get_next_sequence, _sequence_counter

        # Clear any existing state
        _sequence_counter.clear()
        instance_id = "test-instance-2"

        seq1 = get_next_sequence(instance_id)
        seq2 = get_next_sequence(instance_id)
        seq3 = get_next_sequence(instance_id)

        assert seq1 == 1
        assert seq2 == 2
        assert seq3 == 3

    def test_different_instances_independent_sequences(self):
        """Different instances have independent sequence counters."""
        from daemon.utils import get_next_sequence, _sequence_counter

        # Clear any existing state
        _sequence_counter.clear()

        seq_a1 = get_next_sequence("instance-a")
        seq_b1 = get_next_sequence("instance-b")
        seq_a2 = get_next_sequence("instance-a")

        assert seq_a1 == 1
        assert seq_b1 == 1  # Different instance, starts at 1
        assert seq_a2 == 2  # Same instance continues


# ============================================================================
# Test broadcast_checkpoint_event()
# ============================================================================


class TestBroadcastCheckpointEvent:
    """Tests for EventBus.broadcast_checkpoint_event()."""

    @pytest.mark.asyncio
    async def test_checkpoint_event_queued(self, mock_event_repo, instance_id, messages):
        """Checkpoint event is queued for SSE delivery."""
        from daemon.services.event_bus import EventBus

        event_bus = EventBus(event_repo=mock_event_repo)

        await event_bus.broadcast_checkpoint_event(
            instance_id=instance_id,
            messages=messages,
            checkpoint_id="seq_0",
        )

        events = await event_bus.get_streaming_events(instance_id)
        assert len(events) == 1
        assert events[0]["event_type"] == "checkpoint"
        assert events[0]["checkpoint_id"] == "seq_0"
        assert len(events[0]["messages"]) == 2

    @pytest.mark.asyncio
    async def test_empty_messages_skipped(self, mock_event_repo, instance_id):
        """Empty messages list skips emission."""
        from daemon.services.event_bus import EventBus

        event_bus = EventBus(event_repo=mock_event_repo)

        await event_bus.broadcast_checkpoint_event(
            instance_id=instance_id,
            messages=[],  # Empty
            checkpoint_id="seq_0",
        )

        events = await event_bus.get_streaming_events(instance_id)
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_checkpoint_with_tool_outputs(self, mock_event_repo, instance_id):
        """Checkpoint event includes tool_outputs map."""
        from daemon.services.event_bus import EventBus

        event_bus = EventBus(event_repo=mock_event_repo)
        messages = [
            {"message_id": "msg-1", "role": "assistant", "content": "", "tool_calls": [
                {"id": "tool-1", "name": "bash", "arguments": {"cmd": "ls"}}
            ]},
        ]
        tool_outputs = {"tool-1": "file1.txt\nfile2.txt"}

        await event_bus.broadcast_checkpoint_event(
            instance_id=instance_id,
            messages=messages,
            checkpoint_id="seq_1",
            tool_outputs=tool_outputs,
        )

        events = await event_bus.get_streaming_events(instance_id)
        assert events[0]["tool_outputs"]["tool-1"] == "file1.txt\nfile2.txt"

    @pytest.mark.asyncio
    async def test_notification_set_on_checkpoint(self, mock_event_repo, instance_id, messages):
        """Notification is set after broadcasting checkpoint."""
        from daemon.services.event_bus import EventBus

        event_bus = EventBus(event_repo=mock_event_repo)
        notification = event_bus.get_notification(instance_id)

        assert not notification.is_set()

        await event_bus.broadcast_checkpoint_event(
            instance_id=instance_id,
            messages=messages,
            checkpoint_id="seq_0",
        )

        assert notification.is_set()

    @pytest.mark.asyncio
    async def test_multiple_checkpoints_queued(self, mock_event_repo, instance_id):
        """Multiple checkpoints are queued in order."""
        from daemon.services.event_bus import EventBus

        event_bus = EventBus(event_repo=mock_event_repo)

        for i in range(3):
            messages = [{"message_id": f"msg-{i}", "role": "user", "content": f"Test {i}"}]
            await event_bus.broadcast_checkpoint_event(
                instance_id=instance_id,
                messages=messages,
                checkpoint_id=f"seq_{i}",
            )

        events = await event_bus.get_streaming_events(instance_id)
        assert len(events) == 3
        assert events[0]["checkpoint_id"] == "seq_0"
        assert events[1]["checkpoint_id"] == "seq_1"
        assert events[2]["checkpoint_id"] == "seq_2"
