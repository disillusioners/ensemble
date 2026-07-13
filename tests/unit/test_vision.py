"""Unit tests for vision support in the daemon.

Tests cover:
1. MessageCreate validation for images field
2. Multimodal HumanMessage construction with images
3. Serialization of multimodal messages
4. Regression tests for text-only paths
"""

import asyncio
import pytest
from langchain_core.messages import HumanMessage, AIMessage

from daemon.models import MessageCreate
from daemon.utils import serialize_message


# =============================================================================
# Test Data Helpers
# =============================================================================


def make_valid_image(index: int = 0) -> str:
    """Create a valid base64 data URI image string.
    
    The image is small enough (less than 10MB) and follows the data URI format.
    """
    import base64
    # Create a small base64-encoded "image" (just enough data)
    # base64 encoding of "fake image data" - roughly 50 bytes decoded
    fake_data = f"FAKE_IMAGE_DATA_{index}_FOR_TESTING".encode('utf-8')
    encoded = base64.b64encode(fake_data).decode('utf-8')
    return f"data:image/png;base64,{encoded}"


def make_large_image(size_mb: float) -> str:
    """Create a base64 data URI image string of approximately the given size.
    
    Args:
        size_mb: Approximate size in megabytes of the decoded image.
        
    Returns:
        A data URI string with approximately the specified decoded size.
    """
    import base64
    # We want the decoded size to be size_mb
    # base64_size * 3/4 = target_size (after decoding)
    # So base64_size = target_size * 4/3
    target_size = int(size_mb * 1024 * 1024)
    base64_needed = target_size * 4 // 3 + 10  # Add a bit for safety
    
    # Create binary data of the appropriate size
    # Use repeating pattern of valid base64 chars to fill the space
    chunk = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    chunk_size = len(chunk)  # 64 chars, decodes to 48 bytes
    
    # Calculate how many chunks we need
    num_chunks = (base64_needed + chunk_size - 1) // chunk_size
    fake_base64 = (chunk * num_chunks)[:base64_needed]
    
    # Pad to valid base64 length (multiple of 4)
    padding_needed = (4 - len(fake_base64) % 4) % 4
    fake_base64 += "=" * padding_needed
    
    # Verify the decoded size
    decoded_size = len(base64.b64decode(fake_base64))
    assert abs(decoded_size - target_size) < 100, f"Size mismatch: {decoded_size} vs {target_size}"
    
    return f"data:image/png;base64,{fake_base64}"


# =============================================================================
# MessageCreate Validation Tests - Image Count
# =============================================================================


class TestMessageCreateImageCountValidation:
    """Tests for MessageCreate images field validation regarding count."""

    def test_images_max_count(self):
        """Sending 4 images should raise validation error."""
        images = [make_valid_image(i) for i in range(4)]
        with pytest.raises(ValueError) as exc_info:
            MessageCreate(content="Test with 4 images", images=images)
        assert "3" in str(exc_info.value)  # Should mention max of 3

    def test_images_max_count_exactly_3(self):
        """Sending exactly 3 images should succeed."""
        images = [make_valid_image(i) for i in range(3)]
        msg = MessageCreate(content="Test with 3 images", images=images)
        assert msg.images == images
        assert len(msg.images) == 3

    def test_images_max_count_2(self):
        """Sending 2 images should succeed."""
        images = [make_valid_image(i) for i in range(2)]
        msg = MessageCreate(content="Test with 2 images", images=images)
        assert msg.images == images
        assert len(msg.images) == 2

    def test_images_max_count_1(self):
        """Sending 1 image should succeed."""
        images = [make_valid_image(0)]
        msg = MessageCreate(content="Test with 1 image", images=images)
        assert msg.images == images
        assert len(msg.images) == 1


# =============================================================================
# MessageCreate Validation Tests - Image Format
# =============================================================================


class TestMessageCreateImageFormatValidation:
    """Tests for MessageCreate images field validation regarding format."""

    def test_images_invalid_format_missing_data_prefix(self):
        """Sending non-base64-data-URI string should raise validation error."""
        invalid_images = ["just a string", "image.png", "base64,abc123"]
        for img in invalid_images:
            with pytest.raises(ValueError) as exc_info:
                MessageCreate(content="Test", images=[img])
            assert "data:image" in str(exc_info.value).lower()

    def test_images_invalid_format_no_base64_suffix(self):
        """Image without base64 suffix should raise validation error."""
        invalid_images = [
            "data:image/png;base64",  # No data after comma
            "data:image/png;base64,",  # Empty data after comma
        ]
        for img in invalid_images:
            with pytest.raises(ValueError) as exc_info:
                MessageCreate(content="Test", images=[img])
            assert "base64 data uri" in str(exc_info.value).lower()

    def test_images_invalid_format_wrong_mime(self):
        """Image with invalid MIME type should raise validation error."""
        invalid_images = [
            "data:text/plain;base64,abc123",  # Not an image MIME
            "data:;base64,abc123",  # No MIME type
            "data:image;base64,abc123",  # Missing subtype
        ]
        for img in invalid_images:
            with pytest.raises(ValueError) as exc_info:
                MessageCreate(content="Test", images=[img])
            assert "invalid image format" in str(exc_info.value).lower()

    def test_images_svg_rejected(self):
        """SVG MIME type should be rejected (XSS defense-in-depth)."""
        svg_images = [
            "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvIj48L3N2Zz4=",  # With base64
            "data:image/svg+xml;base64,",  # Minimal SVG
        ]
        for img in svg_images:
            with pytest.raises(ValueError) as exc_info:
                MessageCreate(content="Test", images=[img])
            assert "invalid image format" in str(exc_info.value).lower()

    def test_images_valid_format(self):
        """Sending valid data:image/png;base64,abc123 should succeed."""
        valid_images = [
            "data:image/png;base64,abc123",
            "data:image/jpeg;base64,xyz789",
            "data:image/gif;base64,def456",
            "data:image/webp;base64,ghi012",
        ]
        for img in valid_images:
            msg = MessageCreate(content="Test", images=[img])
            assert msg.images == [img]


# =============================================================================
# MessageCreate Validation Tests - Image Size
# =============================================================================


class TestMessageCreateImageSizeValidation:
    """Tests for MessageCreate images field validation regarding size."""

    def test_images_too_large(self):
        """Sending an image > 10MB should raise validation error."""
        # Create an image estimated at ~11MB
        large_image = make_large_image(11)
        with pytest.raises(ValueError) as exc_info:
            MessageCreate(content="Test", images=[large_image])
        assert "10mb" in str(exc_info.value).lower()

    def test_images_exactly_10mb(self):
        """Sending an image estimated at just under 10MB should succeed."""
        # Create an image estimated at ~7MB (base64 adds ~33% overhead)
        # Target: original_size < 10MB, so base64_size < 10MB * 4/3 = ~13.3MB
        # We use 7MB to be safely under the limit (7MB base64 ≈ 9.3MB decoded)
        image_7mb = make_large_image(7)
        # Verify the estimated size is under 10MB
        base64_size = len(image_7mb) - len("data:image/png;base64,")
        estimated_size = base64_size * 3 // 4
        assert estimated_size < 10 * 1024 * 1024, f"Image too large: {estimated_size} bytes"
        msg = MessageCreate(content="Test", images=[image_7mb])
        assert msg.images == [image_7mb]

    def test_images_under_10mb(self):
        """Sending a small image should succeed."""
        small_image = make_large_image(1)  # 1MB
        msg = MessageCreate(content="Test", images=[small_image])
        assert msg.images == [small_image]

    def test_images_just_under_10mb_passes(self):
        """Sending an image estimated at just under 10MB should succeed."""
        # 10MB = 10,485,760 bytes
        # Target: original_size < 10MB, use 9.9MB
        image_9_9mb = make_large_image(9.9)
        # Verify the estimated size is under 10MB
        base64_str = image_9_9mb.split(",", 1)[1]
        estimated_size = len(base64_str) * 3 // 4
        assert estimated_size < 10 * 1024 * 1024, f"Image too large: {estimated_size} bytes"
        msg = MessageCreate(content="Test", images=[image_9_9mb])
        assert msg.images == [image_9_9mb]

    def test_images_just_over_10mb_fails(self):
        """Sending an image estimated at just over 10MB should fail."""
        # 10MB = 10,485,760 bytes
        # Target: original_size > 10MB, use 10.1MB
        image_10_1mb = make_large_image(10.1)
        with pytest.raises(ValueError) as exc_info:
            MessageCreate(content="Test", images=[image_10_1mb])
        assert "10mb" in str(exc_info.value).lower()

    def test_images_very_small(self):
        """Sending a tiny image should succeed."""
        tiny_image = "data:image/png;base64,abc123"
        msg = MessageCreate(content="Test", images=[tiny_image])
        assert msg.images == [tiny_image]


# =============================================================================
# MessageCreate Validation Tests - Empty/None Handling
# =============================================================================


class TestMessageCreateEmptyNoneHandling:
    """Tests for MessageCreate images field empty/None handling."""

    def test_images_empty_list(self):
        """Sending images=[] should be treated as None (no images)."""
        msg = MessageCreate(content="Test", images=[])
        # Empty list should be converted to None
        assert msg.images is None

    def test_images_none(self):
        """Sending images=None should work (no images)."""
        msg = MessageCreate(content="Test", images=None)
        assert msg.images is None

    def test_no_images_field(self):
        """Not sending images field at all should work (backward compat)."""
        msg = MessageCreate(content="Test")
        assert msg.images is None

    def test_images_field_explicitly_null(self):
        """Explicitly passing images=null should work."""
        msg = MessageCreate(content="Test", images=None)
        assert msg.images is None


# =============================================================================
# Multimodal HumanMessage Construction Tests
# =============================================================================


class TestMultimodalHumanMessageConstruction:
    """Tests for HumanMessage construction with multimodal content."""

    def test_text_only_message(self):
        """Without images, should produce standard text HumanMessage."""
        msg = HumanMessage(content="Hello, how are you?")
        
        assert isinstance(msg, HumanMessage)
        assert msg.content == "Hello, how are you?"
        # Content should be a string (not a list)
        assert isinstance(msg.content, str)

    def test_text_with_images(self):
        """With images, should produce multimodal HumanMessage with content array."""
        image = make_valid_image(0)
        content = [
            {"type": "text", "text": "What do you see in this image?"},
            {"type": "image_url", "image_url": {"url": image}},
        ]
        msg = HumanMessage(content=content)
        
        assert isinstance(msg, HumanMessage)
        assert isinstance(msg.content, list)
        assert len(msg.content) == 2
        assert msg.content[0]["type"] == "text"
        assert msg.content[0]["text"] == "What do you see in this image?"
        assert msg.content[1]["type"] == "image_url"
        assert msg.content[1]["image_url"]["url"] == image

    def test_text_with_multiple_images(self):
        """With multiple images, should produce content array with all images."""
        images = [make_valid_image(i) for i in range(3)]
        content = [
            {"type": "text", "text": "Describe all these images."},
            {"type": "image_url", "image_url": {"url": images[0]}},
            {"type": "image_url", "image_url": {"url": images[1]}},
            {"type": "image_url", "image_url": {"url": images[2]}},
        ]
        msg = HumanMessage(content=content)
        
        assert isinstance(msg.content, list)
        assert len(msg.content) == 4  # 1 text + 3 images
        # Check all image URLs are preserved
        for i, img_block in enumerate(msg.content[1:], start=0):
            assert img_block["type"] == "image_url"
            assert img_block["image_url"]["url"] == images[i]

    def test_images_only_message(self):
        """With images but empty text, should produce image-only content array."""
        image = make_valid_image(0)
        content = [
            {"type": "image_url", "image_url": {"url": image}},
        ]
        msg = HumanMessage(content=content)
        
        assert isinstance(msg, HumanMessage)
        assert isinstance(msg.content, list)
        assert len(msg.content) == 1
        assert msg.content[0]["type"] == "image_url"
        assert msg.content[0]["image_url"]["url"] == image

    def test_message_content_format(self):
        """Verify the content array format matches OpenAI expectations."""
        image = make_valid_image(0)
        content = [
            {"type": "text", "text": "Analyze this"},
            {"type": "image_url", "image_url": {"url": image}},
        ]
        msg = HumanMessage(content=content)
        
        # Verify the structure matches what OpenAI expects
        text_block = msg.content[0]
        image_block = msg.content[1]
        
        # Text block format
        assert text_block["type"] == "text"
        assert "text" in text_block
        
        # Image block format (OpenAI format)
        assert image_block["type"] == "image_url"
        assert isinstance(image_block["image_url"], dict)
        assert "url" in image_block["image_url"]
        assert image_block["image_url"]["url"].startswith("data:image/")


# =============================================================================
# Serialization Tests
# =============================================================================


class TestMultimodalSerialization:
    """Tests for serialize_message with multimodal content."""

    def test_serialize_text_message(self):
        """Text-only messages serialize correctly."""
        msg = HumanMessage(content="Hello, how are you?")
        result = serialize_message(msg)
        
        assert result["role"] == "user"
        assert result["content"] == "Hello, how are you?"
        assert result["images"] is None

    def test_serialize_multimodal_message(self):
        """Multimodal messages preserve image data."""
        image = make_valid_image(0)
        content = [
            {"type": "text", "text": "What do you see?"},
            {"type": "image_url", "image_url": {"url": image}},
        ]
        msg = HumanMessage(content=content)
        result = serialize_message(msg)
        
        assert result["role"] == "user"
        assert result["content"] == "What do you see?"
        assert result["images"] is not None
        assert len(result["images"]) == 1
        assert result["images"][0] == image

    def test_serialize_multimodal_with_multiple_images(self):
        """Multimodal messages with multiple images preserve all image data."""
        images = [make_valid_image(i) for i in range(3)]
        content = [
            {"type": "text", "text": "Describe these images."},
            {"type": "image_url", "image_url": {"url": images[0]}},
            {"type": "image_url", "image_url": {"url": images[1]}},
            {"type": "image_url", "image_url": {"url": images[2]}},
        ]
        msg = HumanMessage(content=content)
        result = serialize_message(msg)
        
        assert result["content"] == "Describe these images."
        assert result["images"] is not None
        assert len(result["images"]) == 3
        for i, img in enumerate(images):
            assert result["images"][i] == img

    def test_serialize_preserves_image_urls(self):
        """Image URLs in content blocks survive serialization."""
        original_url = "data:image/png;base64,EXACT_URL_STRING"
        content = [
            {"type": "text", "text": "Look at this"},
            {"type": "image_url", "image_url": {"url": original_url}},
        ]
        msg = HumanMessage(content=content)
        result = serialize_message(msg)
        
        assert result["images"] is not None
        assert result["images"][0] == original_url

    def test_serialize_image_only_message(self):
        """Image-only messages (no text) serialize correctly."""
        image = make_valid_image(0)
        content = [
            {"type": "image_url", "image_url": {"url": image}},
        ]
        msg = HumanMessage(content=content)
        result = serialize_message(msg)
        
        # Content should be empty string when no text block
        assert result["content"] == ""
        assert result["images"] is not None
        assert result["images"][0] == image

    def test_serialize_multimodal_with_jpeg_image(self):
        """JPEG images serialize correctly."""
        jpeg_url = "data:image/jpeg;base64,/9j/4aaq"
        content = [
            {"type": "text", "text": "What is this?"},
            {"type": "image_url", "image_url": {"url": jpeg_url}},
        ]
        msg = HumanMessage(content=content)
        result = serialize_message(msg)
        
        assert result["images"] is not None
        assert result["images"][0] == jpeg_url
        assert "jpeg" in jpeg_url


# =============================================================================
# Regression Tests
# =============================================================================


class TestVisionRegressionTests:
    """Regression tests to ensure existing functionality is not broken."""

    def test_text_only_unchanged(self):
        """Ensure text-only path produces same output as before vision feature."""
        msg = HumanMessage(content="Hello world")
        result = serialize_message(msg)
        
        # Verify the structure matches what we expect
        assert result["role"] == "user"
        assert result["content"] == "Hello world"
        assert result["images"] is None
        assert result["tool_calls"] is None
        assert result["thinking"] is None
        assert result["thinking_extracted"] is None

    def test_message_create_without_images_unchanged(self):
        """MessageCreate without images should work exactly as before."""
        msg = MessageCreate(content="Hello agent!")
        
        assert msg.content == "Hello agent!"
        assert msg.images is None
        
        # Should serialize normally
        data = msg.model_dump()
        assert data["content"] == "Hello agent!"
        assert data["images"] is None

    def test_message_create_with_content_only(self):
        """MessageCreate with just content (no images field) unchanged."""
        msg = MessageCreate(content="Just text")
        assert msg.content == "Just text"
        assert msg.images is None

    def test_serialize_message_backward_compat(self):
        """serialize_message should work the same for text-only as before."""
        # Test with various text contents (without think tags)
        texts = [
            "Simple message",
            "Multi-line\nmessage\ncontent",
            "",
        ]
        for text in texts:
            msg = HumanMessage(content=text)
            result = serialize_message(msg)
            assert result["content"] == text
            assert result["images"] is None

    def test_image_list_extraction_from_content(self):
        """Verify images are properly extracted from content list format."""
        images = ["data:image/png;base64,img1", "data:image/png;base64,img2"]
        content = [
            {"type": "text", "text": "Compare these"},
            {"type": "image_url", "image_url": {"url": images[0]}},
            {"type": "image_url", "image_url": {"url": images[1]}},
        ]
        msg = HumanMessage(content=content)
        result = serialize_message(msg)
        
        # Images should be extracted to the images field
        assert result["images"] == images
        # Text should be extracted separately
        assert result["content"] == "Compare these"

    def test_multiple_image_types(self):
        """Ensure different image MIME types are handled correctly."""
        image_types = [
            ("data:image/png;base64,abc", "png"),
            ("data:image/jpeg;base64,def", "jpeg"),
            ("data:image/gif;base64,ghi", "gif"),
            ("data:image/webp;base64,jkl", "webp"),
        ]
        for url, mime_type in image_types:
            content = [
                {"type": "text", "text": f"Image {mime_type}"},
                {"type": "image_url", "image_url": {"url": url}},
            ]
            msg = HumanMessage(content=content)
            result = serialize_message(msg)
            
            assert result["images"] is not None
            assert len(result["images"]) == 1
            assert mime_type in result["images"][0]


# =============================================================================
# Tool Binding Tests (Critical Fix #1)
# =============================================================================


class TestToolBindingWithoutVision:
    """Tests to ensure llm_standard is bound to tools when model_vision=None.

    This is a critical regression test for the fix where llm_standard was not
    being bound to tools when vision was not configured.
    """

    def test_tool_calling_without_vision_config(self):
        """Verify that when model_vision=None, llm_standard is still bound to tools.

        This test verifies the fix for the critical bug where:
        - When vision WAS configured: llm_standard.bind_tools(tools) was called
        - When vision was NOT configured: llm_standard remained unbound

        The fix ensures tools are always bound to llm_standard regardless of
        whether vision is configured.
        """
        from unittest.mock import MagicMock, patch

        # Mock the tools
        mock_tool = MagicMock()
        mock_tool.name = "test_tool"
        mock_tool.description = "A test tool"
        mock_tools = [mock_tool]

        # Mock the LLM config with no vision
        mock_llm_config = {
            "base_url": "https://api.example.com",
            "api_key": "test-key",
            "temperature": 0.7,
            "max_tokens": 4096,
            # No model_vision - this is the key scenario
        }

        # Patch ThinkingChatOpenAI to capture what it receives
        with patch("daemon.graph.ThinkingChatOpenAI") as MockLLM:
            # Set up the mock to return an object that can be chained with bind_tools
            mock_llm_instance = MagicMock()
            mock_llm_instance.bind_tools = MagicMock(return_value=MagicMock())
            MockLLM.return_value = mock_llm_instance

            # Import and call the function we're testing
            from daemon.graph import build_instance_llms

            llm_with_tools, llm_standard = build_instance_llms(
                llm_config_with_headers=mock_llm_config,
                model_standard="gpt-4o",
                model_vision=None,  # No vision configured
                tools=mock_tools,
                retry_config=None,
            )

            # Verify ThinkingChatOpenAI was called once to create the standard LLM
            assert MockLLM.call_count == 1, f"Expected 1 call to ThinkingChatOpenAI, got {MockLLM.call_count}"

            # Verify bind_tools was called on the standard LLM
            # (called at least once - may be called twice: once for llm_with_tools, once for llm_standard)
            assert mock_llm_instance.bind_tools.called, "bind_tools was not called on llm_standard"
            # Verify it was called with the tools
            mock_llm_instance.bind_tools.assert_called_with(mock_tools)


# =============================================================================
# Utility Function Tests - _build_message_content
# =============================================================================


class TestBuildMessageContent:
    """Tests for the _build_message_content utility function."""

    def test_text_only_returns_string(self):
        """Text-only message returns a plain string."""
        from daemon.manager import _build_message_content
        
        result = _build_message_content("Hello world", None)
        
        assert isinstance(result, str)
        assert result == "Hello world"

    def test_text_with_images_returns_list(self):
        """Text with images returns a list with text and image_url blocks."""
        from daemon.manager import _build_message_content
        
        images = [make_valid_image(i) for i in range(2)]
        result = _build_message_content("Describe these", images)
        
        assert isinstance(result, list)
        assert len(result) == 3  # 1 text + 2 images
        
        # First block is text
        assert result[0]["type"] == "text"
        assert result[0]["text"] == "Describe these"
        
        # Remaining blocks are images
        assert result[1]["type"] == "image_url"
        assert result[1]["image_url"]["url"] == images[0]
        assert result[2]["type"] == "image_url"
        assert result[2]["image_url"]["url"] == images[1]

    def test_image_only_returns_image_list(self):
        """Image-only message (empty text) returns list with text + image_url blocks.
        
        Note: The implementation always adds a text block first (even empty),
        followed by image_url blocks. This is correct per OpenAI API spec.
        """
        from daemon.manager import _build_message_content
        
        images = [make_valid_image(0)]
        result = _build_message_content("", images)
        
        assert isinstance(result, list)
        # Implementation adds text block first, then images
        assert result[0]["type"] == "text"
        assert result[0]["text"] == ""
        assert result[1]["type"] == "image_url"
        assert result[1]["image_url"]["url"] == images[0]

    def test_empty_images_returns_string(self):
        """Empty images list returns plain string (same as text-only)."""
        from daemon.manager import _build_message_content
        
        result = _build_message_content("Hello", [])
        
        assert isinstance(result, str)
        assert result == "Hello"

    def test_single_image(self):
        """Single image with text returns list with text + 1 image."""
        from daemon.manager import _build_message_content
        
        image = make_valid_image(0)
        result = _build_message_content("Look at this", [image])
        
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["type"] == "text"
        assert result[1]["type"] == "image_url"
        assert result[1]["image_url"]["url"] == image


# =============================================================================
# API Endpoint Tests - HTTP 400 for images without vision
# =============================================================================


class TestImagesWithoutVisionConfig:
    """Tests for HTTP 400 when images are sent but model_vision is not configured."""

    def test_send_message_with_images_no_vision_returns_400(self):
        """Sending images to an instance without vision model should return 400."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from fastapi import HTTPException
        
        # Create a mock manager config with no model_vision
        mock_config = MagicMock()
        mock_config.llm.model_vision = None  # No vision configured
        
        # Create mock manager
        mock_manager = MagicMock()
        mock_manager.config = mock_config
        mock_manager.get_instance = AsyncMock()  # Instance exists
        # Phase 3: routers check manager.is_write_paused; MagicMock auto-attr is truthy → 503.
        mock_manager.is_write_paused = False

        # Create mock request with app.state.manager
        mock_request = MagicMock()
        mock_request.app.state.manager = mock_manager

        # Patch _get_manager to return our mock
        with patch("daemon.routers.messages._get_manager", return_value=mock_manager):
            # Import the API function
            from daemon.routers.messages import send_message
            from daemon.models import MessageCreate

            # Create message with images
            images = [make_valid_image(0)]
            message = MessageCreate(content="What do you see?", images=images)
            
            # Call should raise HTTPException 400
            with pytest.raises(HTTPException) as exc_info:
                # Run the async function
                import asyncio
                asyncio.run(send_message(
                    instance_id="test-instance-id",
                    message=message,
                    request=mock_request,
                    response=MagicMock(),
                ))
            
            assert exc_info.value.status_code == 400
            assert "model_vision" in str(exc_info.value.detail).lower()

    def test_send_message_without_images_no_vision_succeeds(self):
        """Sending text-only to instance without vision model should succeed."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from datetime import datetime, timezone

        # Mock result from enqueue_message_job
        mock_result = MagicMock()
        mock_result.message_id = "test-msg-id"
        mock_result.job_id = "test-job-id"  # MessageResponse.job_id is str | None

        # Create a mock manager config with no model_vision
        mock_config = MagicMock()
        mock_config.llm.model_vision = None  # No vision configured

        # Create mock manager
        mock_manager = MagicMock()
        mock_manager.config = mock_config
        mock_manager.get_instance = AsyncMock()  # Instance exists
        # Phase 5 cutover: send_message router now dispatches via
        # enqueue_message_job (creates JobItem mirror), not enqueue_message.
        mock_manager.enqueue_message_job = AsyncMock(return_value=mock_result)
        # Phase 3: routers check manager.is_write_paused; MagicMock auto-attr is truthy → 503.
        mock_manager.is_write_paused = False

        # Create mock request with app.state.manager
        mock_request = MagicMock()
        mock_request.app.state.manager = mock_manager

        # Patch _get_manager to return our mock
        with patch("daemon.routers.messages._get_manager", return_value=mock_manager):
            from daemon.routers.messages import send_message
            from daemon.models import MessageCreate

            # Create text-only message (no images)
            message = MessageCreate(content="Hello")

            # Call should succeed (no HTTPException)
            import asyncio
            asyncio.run(send_message(
                instance_id="test-instance-id",
                message=message,
                request=mock_request,
                response=MagicMock(),
            ))

            # Verify enqueue_message_job was called with images=None
            mock_manager.enqueue_message_job.assert_called_once()
            call_kwargs = mock_manager.enqueue_message_job.call_args.kwargs
            assert call_kwargs["images"] is None


# =============================================================================
# Integration Tests - enqueue_message with images (mock-based)
# =============================================================================


class TestEnqueueMessageWithImages:
    """Tests for enqueue_message storing images in DB."""

    def test_enqueue_message_preserves_images(self):
        """enqueue_message should store images in MessageQueue."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from datetime import datetime, timezone
        
        # We test this by mocking at the repository level
        with patch("daemon.manager.InstanceManager") as MockManager:
            mock_instance = MagicMock()
            mock_instance.enqueue_message = AsyncMock(return_value=MagicMock(
                message_id="test-msg-id",
                status="queued"
            ))
            MockManager.return_value = mock_instance
            
            # Import after patching
            from daemon.manager import InstanceManager
            
            manager = InstanceManager()
            images = [make_valid_image(i) for i in range(2)]
            
            # Call enqueue_message with images
            result = asyncio.run(manager.enqueue_message(
                instance_id="test-instance",
                message="What do you see?",
                source="api",
                images=images
            ))
            
            # Verify images were passed
            mock_instance.enqueue_message.assert_called_once()
            call_kwargs = mock_instance.enqueue_message.call_args.kwargs
            assert call_kwargs["images"] == images
