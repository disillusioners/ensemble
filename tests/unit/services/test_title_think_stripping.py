"""Tests for <think>-block stripping in TitleGenerationService.

Reasoning models (DeepSeek, GLM, QwQ) may emit their reasoning as
<think>...</think> inside the response content. The title should be the
visible text only. If the response is thinking-only, no title is set
(graceful skip).
"""

import pytest
from unittest.mock import MagicMock, patch

from daemon.services.title_generation import TitleGenerationService


@pytest.fixture
def mock_manager():
    """Mock manager with repository + LLM config wired up.

    The repository's `get` returns a meta object with NO existing title,
    so generation proceeds. `update_title` is a sync MagicMock because
    the service calls it via asyncio.to_thread (it's a sync repository
    method).
    """
    manager = MagicMock()
    mock_meta = MagicMock()
    mock_meta.instance_metadata = {}  # no existing title
    manager._instance_repository.get = MagicMock(return_value=mock_meta)
    manager._instance_repository.update_title = MagicMock()

    manager.config = MagicMock()
    manager.config.llm.base_url = "https://api.openai.com/v1"
    manager.config.llm.api_key = "test-key"
    manager.config.llm.model = "gpt-4"
    manager.config.llm.model_title = "gpt-4"
    return manager


def _make_mock_llm(response_content):
    """Build a mock ThinkingChatOpenAI that returns the given content.

    `invoke` is a synchronous MagicMock (mirroring the real client which
    is wrapped in asyncio.to_thread by the caller).
    """
    llm = MagicMock()
    llm.invoke = MagicMock(return_value=MagicMock(content=response_content))
    return llm


@pytest.mark.asyncio
async def test_title_with_think_block_strips_reasoning(mock_manager):
    """When the LLM returns <think>...</think>Visible Title, only the visible
    text is stored as the title.
    """
    service = TitleGenerationService(manager=mock_manager)

    raw_content = "<think>Let me think about this for a moment</think>Fix Login Bug"
    mock_llm = _make_mock_llm(raw_content)

    with patch(
        "daemon.services.title_generation.ThinkingChatOpenAI",
        return_value=mock_llm,
    ):
        await service._generate_and_broadcast_title(
            "instance-abc", "Please fix the login bug"
        )

    # update_title should have been called with only the visible text
    mock_manager._instance_repository.update_title.assert_called_once()
    args, _kwargs = mock_manager._instance_repository.update_title.call_args
    assert args[0] == "instance-abc"
    assert args[1] == "Fix Login Bug"


@pytest.mark.asyncio
async def test_title_think_only_response_skips_gracefully(mock_manager):
    """When the LLM returns ONLY a <think>...</think> block (no visible
    text), no title is stored — graceful skip.
    """
    service = TitleGenerationService(manager=mock_manager)

    raw_content = "<think>The user is asking about authentication and I should think deeply...</think>"
    mock_llm = _make_mock_llm(raw_content)

    with patch(
        "daemon.services.title_generation.ThinkingChatOpenAI",
        return_value=mock_llm,
    ):
        # Must not raise
        await service._generate_and_broadcast_title(
            "instance-xyz", "Tell me about auth"
        )

    # update_title should NOT have been called — graceful skip
    mock_manager._instance_repository.update_title.assert_not_called()


@pytest.mark.asyncio
async def test_title_plain_response_unchanged(mock_manager):
    """Regression guard: a plain (no-think) response still gets stored
    unchanged — the think-stripping should be a no-op.
    """
    service = TitleGenerationService(manager=mock_manager)

    raw_content = "Fix Login Bug"
    mock_llm = _make_mock_llm(raw_content)

    with patch(
        "daemon.services.title_generation.ThinkingChatOpenAI",
        return_value=mock_llm,
    ):
        await service._generate_and_broadcast_title(
            "instance-plain", "Please fix the login bug"
        )

    mock_manager._instance_repository.update_title.assert_called_once()
    args, _kwargs = mock_manager._instance_repository.update_title.call_args
    assert args[1] == "Fix Login Bug"


@pytest.mark.asyncio
async def test_title_with_multiple_think_blocks(mock_manager):
    """Multiple <think> blocks are all stripped; only the visible text remains."""
    service = TitleGenerationService(manager=mock_manager)

    raw_content = (
        "<think>First thought</think>"
        "<think>Second thought</think>"
        "Deploy Auth Patch"
    )
    mock_llm = _make_mock_llm(raw_content)

    with patch(
        "daemon.services.title_generation.ThinkingChatOpenAI",
        return_value=mock_llm,
    ):
        await service._generate_and_broadcast_title(
            "instance-multi", "Deploy the auth patch"
        )

    mock_manager._instance_repository.update_title.assert_called_once()
    args, _kwargs = mock_manager._instance_repository.update_title.call_args
    assert args[1] == "Deploy Auth Patch"