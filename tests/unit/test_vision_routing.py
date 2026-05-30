"""Unit tests for vision routing logic in daemon/graph.py.

Tests cover the vision model routing logic in create_agent_node():
- Vision model selected when images are present (turn 1+)
- Standard model selected for text-only messages
- Proper fallback behavior when model_vision or llm_standard is None
- Images on later conversation turns (the core fix - no is_first_call check)
- Multiple images and mixed content handling

The key behavior being tested:
    use_vision_model = has_images and model_vision and llm_standard is not None
    current_llm = llm_with_tools if use_vision_model else (llm_standard or llm_with_tools)
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


class TestVisionRoutingWithImages:
    """Test vision model selection when images are present in messages."""

    @pytest.fixture
    def mock_llm_with_tools(self):
        """Create mock LLM with tools (vision model)."""
        mock = MagicMock()
        mock.invoke = MagicMock(return_value=AIMessage(content="Response"))
        return mock

    @pytest.fixture
    def mock_llm_standard(self):
        """Create mock standard LLM."""
        mock = MagicMock()
        mock.invoke = MagicMock(return_value=AIMessage(content="Standard Response"))
        return mock

    @pytest.fixture
    def llm_config_with_vision(self):
        """LLM config with model_vision configured."""
        return {
            "model": "gpt-4o",
            "model_vision": "gpt-4o",
        }

    @pytest.mark.asyncio
    async def test_vision_model_selected_when_images_present(
        self, mock_llm_with_tools, mock_llm_standard, llm_config_with_vision
    ):
        """Test 1: Vision model selected when messages contain image_url blocks."""
        from daemon.graph import create_agent_node

        # Create message with image content
        messages = [
            HumanMessage(content=[
                {"type": "text", "text": "What's in this image?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
            ])
        ]

        agent_node = create_agent_node(
            mock_llm_with_tools,
            system_prompt="You are helpful.",
            llm_config=llm_config_with_vision,
            llm_standard=mock_llm_standard,
        )

        await agent_node({"messages": messages})

        # Vision model (llm_with_tools) should be called
        mock_llm_with_tools.invoke.assert_called_once()
        mock_llm_standard.invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_standard_model_selected_for_text_only(
        self, mock_llm_with_tools, mock_llm_standard, llm_config_with_vision
    ):
        """Test 2: Standard model selected when messages are text-only."""
        from daemon.graph import create_agent_node

        # Create plain text message (no images)
        messages = [
            HumanMessage(content="Hello, how are you?"),
        ]

        agent_node = create_agent_node(
            mock_llm_with_tools,
            system_prompt="You are helpful.",
            llm_config=llm_config_with_vision,
            llm_standard=mock_llm_standard,
        )

        await agent_node({"messages": messages})

        # Standard model should be called
        mock_llm_standard.invoke.assert_called_once()
        mock_llm_with_tools.invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_model_vision_configured_falls_back_to_standard(
        self, mock_llm_with_tools, mock_llm_standard
    ):
        """Test 3: When model_vision is None/not in config, falls back to standard model."""
        from daemon.graph import create_agent_node

        # Config without model_vision
        llm_config = {
            "model": "gpt-4o",
            # No model_vision key
        }

        messages = [
            HumanMessage(content=[
                {"type": "text", "text": "Image analysis?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
            ])
        ]

        agent_node = create_agent_node(
            mock_llm_with_tools,
            system_prompt="You are helpful.",
            llm_config=llm_config,
            llm_standard=mock_llm_standard,
        )

        await agent_node({"messages": messages})

        # Should fall back to standard model even with images present
        mock_llm_standard.invoke.assert_called_once()
        mock_llm_with_tools.invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_standard_is_none_falls_back_to_llm_with_tools(
        self, mock_llm_with_tools, llm_config_with_vision
    ):
        """Test 4: When llm_standard is None, falls back to llm_with_tools."""
        from daemon.graph import create_agent_node

        messages = [
            HumanMessage(content="Plain text message"),
        ]

        agent_node = create_agent_node(
            mock_llm_with_tools,
            system_prompt="You are helpful.",
            llm_config=llm_config_with_vision,
            llm_standard=None,  # No standard model
        )

        await agent_node({"messages": messages})

        # Should fall back to llm_with_tools
        mock_llm_with_tools.invoke.assert_called_once()


class TestVisionRoutingOnLaterTurns:
    """Test 5: Images on later turns (the core fix - no is_first_call check).

    The key behavioral change: is_first_call was REMOVED.
    Now vision model is used whenever images are present, regardless of
    conversation turn (whether turn 1, 3, 5, etc).
    """

    @pytest.fixture
    def mock_llm_with_tools(self):
        mock = MagicMock()
        mock.invoke = MagicMock(return_value=AIMessage(content="Response"))
        return mock

    @pytest.fixture
    def mock_llm_standard(self):
        mock = MagicMock()
        mock.invoke = MagicMock(return_value=AIMessage(content="Standard Response"))
        return mock

    @pytest.fixture
    def llm_config_with_vision(self):
        return {
            "model": "gpt-4o",
            "model_vision": "gpt-4o",
        }

    @pytest.mark.asyncio
    async def test_images_on_turn_3_vision_model_selected(
        self, mock_llm_with_tools, mock_llm_standard, llm_config_with_vision
    ):
        """Test 5: Images on turn 3+ should still use vision model.

        This is the CORE BEHAVIOR that the feature/vision-always-on branch fixes.
        Previously, is_first_call would be False after the first AIMessage,
        causing images on later turns to use the standard model.
        """
        # Simulate conversation on turn 3:
        # - Turn 1: Human asked something, AI responded
        # - Turn 2: Human asked follow-up, AI responded
        # - Turn 3: Human sends image (current message)
        messages = [
            HumanMessage(content="Hello!"),
            AIMessage(content="Hi there! How can I help?"),
            HumanMessage(content="Follow-up question"),
            AIMessage(content="Let me answer that."),
            # Turn 3: Human sends an image
            HumanMessage(content=[
                {"type": "text", "text": "Can you analyze this?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,xyz789"}},
            ]),
        ]

        from daemon.graph import create_agent_node

        agent_node = create_agent_node(
            mock_llm_with_tools,
            system_prompt="You are helpful.",
            llm_config=llm_config_with_vision,
            llm_standard=mock_llm_standard,
        )

        await agent_node({"messages": messages})

        # Vision model SHOULD be selected because images are present
        # (is_first_call is no longer checked)
        mock_llm_with_tools.invoke.assert_called_once()
        mock_llm_standard.invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_images_on_first_turn(
        self, mock_llm_with_tools, mock_llm_standard, llm_config_with_vision
    ):
        """Test 6: First message has images - vision model selected (unchanged behavior)."""
        messages = [
            HumanMessage(content=[
                {"type": "text", "text": "What's in this?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
            ]),
        ]

        from daemon.graph import create_agent_node

        agent_node = create_agent_node(
            mock_llm_with_tools,
            system_prompt="You are helpful.",
            llm_config=llm_config_with_vision,
            llm_standard=mock_llm_standard,
        )

        await agent_node({"messages": messages})

        # Vision model should be selected (unchanged behavior)
        mock_llm_with_tools.invoke.assert_called_once()
        mock_llm_standard.invoke.assert_not_called()


class TestVisionRoutingEdgeCases:
    """Test edge cases: multiple images, mixed content, etc."""

    @pytest.fixture
    def mock_llm_with_tools(self):
        mock = MagicMock()
        mock.invoke = MagicMock(return_value=AIMessage(content="Response"))
        return mock

    @pytest.fixture
    def mock_llm_standard(self):
        mock = MagicMock()
        mock.invoke = MagicMock(return_value=AIMessage(content="Standard Response"))
        return mock

    @pytest.fixture
    def llm_config_with_vision(self):
        return {
            "model": "gpt-4o",
            "model_vision": "gpt-4o",
        }

    @pytest.mark.asyncio
    async def test_multiple_images_in_one_message(
        self, mock_llm_with_tools, mock_llm_standard, llm_config_with_vision
    ):
        """Test 7: Multiple image_url blocks in one message - vision model selected."""
        messages = [
            HumanMessage(content=[
                {"type": "text", "text": "Compare these images:"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,def456"}},
                {"type": "image_url", "image_url": {"url": "data:image/gif;base64,ghi789"}},
            ]),
        ]

        from daemon.graph import create_agent_node

        agent_node = create_agent_node(
            mock_llm_with_tools,
            system_prompt="You are helpful.",
            llm_config=llm_config_with_vision,
            llm_standard=mock_llm_standard,
        )

        await agent_node({"messages": messages})

        mock_llm_with_tools.invoke.assert_called_once()
        mock_llm_standard.invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_mixed_text_and_image_content(
        self, mock_llm_with_tools, mock_llm_standard, llm_config_with_vision
    ):
        """Test 8: Single message with both text and image_url blocks - vision model selected."""
        messages = [
            HumanMessage(content=[
                {"type": "text", "text": "Here is a screenshot of the error:"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,screenshot"}},
                {"type": "text", "text": "Can you help fix this?"},
            ]),
        ]

        from daemon.graph import create_agent_node

        agent_node = create_agent_node(
            mock_llm_with_tools,
            system_prompt="You are helpful.",
            llm_config=llm_config_with_vision,
            llm_standard=mock_llm_standard,
        )

        await agent_node({"messages": messages})

        mock_llm_with_tools.invoke.assert_called_once()
        mock_llm_standard.invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_image_url_in_aimessage_content(
        self, mock_llm_with_tools, mock_llm_standard, llm_config_with_vision
    ):
        """Test 9: image_url blocks in any message (not just HumanMessage) trigger vision model.

        The routing logic checks ALL messages, not just the latest one.
        This ensures consistency in multimodal conversations.
        """
        # AI message with an image (e.g., multimodal model responding with image)
        messages = [
            HumanMessage(content=[
                {"type": "text", "text": "Image analysis?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
            ]),
            AIMessage(content=[
                {"type": "text", "text": "Here's what I see:"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,ai_generated"}},
            ]),
            HumanMessage(content="Thanks!"),
        ]

        from daemon.graph import create_agent_node

        agent_node = create_agent_node(
            mock_llm_with_tools,
            system_prompt="You are helpful.",
            llm_config=llm_config_with_vision,
            llm_standard=mock_llm_standard,
        )

        await agent_node({"messages": messages})

        # Vision model should be selected because image_url exists in conversation
        mock_llm_with_tools.invoke.assert_called_once()
        mock_llm_standard.invoke.assert_not_called()


class TestVisionRoutingNoImagesInHistory:
    """Test behavior when images exist in conversation history.

    Note: The routing logic checks ALL messages for images, not just the current one.
    If any message in the history has images, the vision model is used.
    This ensures consistent multimodal handling throughout the conversation.
    """

    @pytest.fixture
    def mock_llm_with_tools(self):
        mock = MagicMock()
        mock.invoke = MagicMock(return_value=AIMessage(content="Response"))
        return mock

    @pytest.fixture
    def mock_llm_standard(self):
        mock = MagicMock()
        mock.invoke = MagicMock(return_value=AIMessage(content="Standard Response"))
        return mock

    @pytest.fixture
    def llm_config_with_vision(self):
        return {
            "model": "gpt-4o",
            "model_vision": "gpt-4o",
        }

    @pytest.mark.asyncio
    async def test_text_only_with_multimodal_history_uses_vision(
        self, mock_llm_with_tools, mock_llm_standard, llm_config_with_vision
    ):
        """Text-only message after multimodal history uses vision model.

        The routing logic checks ALL messages for images. Since image existed
        in message history, vision model is used for consistency.
        """
        # History with images
        messages = [
            HumanMessage(content=[
                {"type": "text", "text": "Image 1"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
            ]),
            AIMessage(content="I see a cat."),
            # Current text-only message (follow-up question)
            HumanMessage(content="What breed was it?"),
        ]

        from daemon.graph import create_agent_node

        agent_node = create_agent_node(
            mock_llm_with_tools,
            system_prompt="You are helpful.",
            llm_config=llm_config_with_vision,
            llm_standard=mock_llm_standard,
        )

        await agent_node({"messages": messages})

        # Vision model is used because images exist in conversation history
        mock_llm_with_tools.invoke.assert_called_once()
        mock_llm_standard.invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_image_in_earlier_message_uses_vision(
        self, mock_llm_with_tools, mock_llm_standard, llm_config_with_vision
    ):
        """Image was in message 1, message 2 is text-only - uses vision model.

        The routing logic checks ALL messages for images, so vision model is
        used even when the current message is text-only but earlier messages
        had images.
        """
        messages = [
            # First message with image
            HumanMessage(content=[
                {"type": "text", "text": "See this?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
            ]),
            # Second message is text only
            HumanMessage(content="Tell me more about that."),
        ]

        from daemon.graph import create_agent_node

        agent_node = create_agent_node(
            mock_llm_with_tools,
            system_prompt="You are helpful.",
            llm_config=llm_config_with_vision,
            llm_standard=mock_llm_standard,
        )

        await agent_node({"messages": messages})

        # Vision model is used because image exists in earlier message
        mock_llm_with_tools.invoke.assert_called_once()
        mock_llm_standard.invoke.assert_not_called()


class TestVisionRoutingWithTools:
    """Test that correct LLM (with tools) is used in both cases."""

    @pytest.fixture
    def mock_tools(self):
        """Mock tools list."""
        return [MagicMock(name="get_weather"), MagicMock(name="search")]

    @pytest.mark.asyncio
    async def test_vision_model_has_tools_bound(self, mock_tools):
        """Both vision and standard models should have tools bound (invoke should work)."""
        from daemon.graph import create_agent_node

        # Create mocks that simulate bound tools
        mock_llm_with_tools = MagicMock()
        mock_llm_standard = MagicMock()

        def invoke_side_effect(messages):
            return AIMessage(content="Response with tools")

        mock_llm_with_tools.invoke = MagicMock(side_effect=invoke_side_effect)
        mock_llm_standard.invoke = MagicMock(side_effect=invoke_side_effect)

        llm_config = {
            "model": "gpt-4o",
            "model_vision": "gpt-4o",
        }

        # Test with images
        messages_with_images = [
            HumanMessage(content=[
                {"type": "text", "text": "Image analysis with tools?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
            ]),
        ]

        agent_node = create_agent_node(
            mock_llm_with_tools,
            system_prompt="You are helpful.",
            llm_config=llm_config,
            llm_standard=mock_llm_standard,
        )

        result = await agent_node({"messages": messages_with_images})

        # Verify invoke was called on vision model
        assert mock_llm_with_tools.invoke.call_count == 1
        assert isinstance(result, dict)
        assert "messages" in result

    @pytest.mark.asyncio
    async def test_standard_model_has_tools_bound(self, mock_tools):
        """Standard model should also have tools bound (invoke should work)."""
        from daemon.graph import create_agent_node

        mock_llm_with_tools = MagicMock()
        mock_llm_standard = MagicMock()

        def invoke_side_effect(messages):
            return AIMessage(content="Standard response with tools")

        mock_llm_with_tools.invoke = MagicMock(side_effect=invoke_side_effect)
        mock_llm_standard.invoke = MagicMock(side_effect=invoke_side_effect)

        llm_config = {
            "model": "gpt-4o",
            "model_vision": "gpt-4o",
        }

        # Test with text only
        messages_text_only = [
            HumanMessage(content="Regular text question"),
        ]

        agent_node = create_agent_node(
            mock_llm_with_tools,
            system_prompt="You are helpful.",
            llm_config=llm_config,
            llm_standard=mock_llm_standard,
        )

        result = await agent_node({"messages": messages_text_only})

        # Verify invoke was called on standard model
        assert mock_llm_standard.invoke.call_count == 1
        assert isinstance(result, dict)
        assert "messages" in result
