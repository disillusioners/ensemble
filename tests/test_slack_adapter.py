"""Tests for SlackAdapter implementation."""

import importlib
import json
import pytest
import sys
import time as time_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from daemon.sources.adapters.slack.adapter import (
    SlackAdapter,
    SlackAPIError,
    SlackCapabilityError,
    CircuitOpenError,
)
from daemon.sources.base import (
    SourceConfig,
    SourceStatus,
    IncomingMessage,
    OutgoingMessage,
)


# ==================== Helper Fixtures ====================


def make_slack_config(
    source_id: str = "slack-main",
    bot_token: str = "xoxb-test-token-123",
    app_token: str = "xapp-test-app-token-456",
    default_agent: str = "ari",
    **config_kwargs
) -> SourceConfig:
    """Create a Slack SourceConfig for testing."""
    return SourceConfig(
        source_id=source_id,
        source_type="slack",
        name="Test Slack Workspace",
        config={**config_kwargs, "default_agent": default_agent},
        credentials={
            "bot_token": bot_token,
            "app_token": app_token,
        },
        enabled=True,
    )


@pytest.fixture
def mock_on_message():
    """Create a mock message handler."""
    return AsyncMock()


@pytest.fixture
def slack_config():
    """Create a default Slack config."""
    return make_slack_config()


@pytest.fixture
def mock_source_repo():
    """Create a mock source repository with get_instance_mapping."""
    repo = MagicMock()

    # Default: return a valid mapping
    mock_mapping = MagicMock()
    mock_mapping.mapping_metadata = {
        "slack_channel_id": "C123456",
        "slack_thread_ts": None,
    }
    repo.get_instance_mapping = MagicMock(return_value=mock_mapping)
    return repo


@pytest.fixture
def mock_slack_app():
    """Create a mock slack_bolt App."""
    app = MagicMock()
    app.client = MagicMock()
    app.client.api_call = AsyncMock()
    return app


@pytest.fixture
def mock_socket_mode_handler():
    """Create a mock AsyncSocketModeHandler."""
    handler = MagicMock()
    handler.start_async = AsyncMock()
    handler.close = AsyncMock()
    return handler


@pytest.fixture
def mock_slack_adapter(slack_config, mock_on_message, mock_source_repo):
    """Create a SlackAdapter with mocked dependencies."""
    adapter = SlackAdapter(slack_config, mock_on_message)
    adapter._source_repo = mock_source_repo
    adapter._workspace_id = "T123456"
    adapter._workspace_name = "Test Workspace"
    adapter._bot_user_id = "U123456"
    adapter._bot_name = "test-bot"
    return adapter


# ==================== Initialization Tests ====================


class TestSlackAdapterInit:
    """Tests for SlackAdapter initialization."""

    def test_init_requires_bot_token(self, mock_on_message):
        """Should raise ValueError if bot_token missing."""
        config = SourceConfig(
            source_id="test",
            source_type="slack",
            name="Test",
            config={},
            credentials={"app_token": "xapp-test"},  # No bot_token
        )

        with pytest.raises(ValueError, match="bot_token"):
            SlackAdapter(config, mock_on_message)

    def test_init_requires_app_token(self, mock_on_message):
        """Should raise ValueError if app_token missing."""
        config = SourceConfig(
            source_id="test",
            source_type="slack",
            name="Test",
            config={},
            credentials={"bot_token": "xoxb-test"},  # No app_token
        )

        with pytest.raises(ValueError, match="app_token"):
            SlackAdapter(config, mock_on_message)

    def test_init_validates_bot_token_prefix(self, mock_on_message):
        """bot_token must start with xoxb-."""
        config = SourceConfig(
            source_id="test",
            source_type="slack",
            name="Test",
            config={},
            credentials={
                "bot_token": "invalid-prefix",
                "app_token": "xapp-test",
            },
        )

        with pytest.raises(ValueError, match="xoxb-"):
            SlackAdapter(config, mock_on_message)

    def test_init_validates_app_token_prefix(self, mock_on_message):
        """app_token must start with xapp-."""
        config = SourceConfig(
            source_id="test",
            source_type="slack",
            name="Test",
            config={},
            credentials={
                "bot_token": "xoxb-valid",
                "app_token": "invalid-prefix",
            },
        )

        with pytest.raises(ValueError, match="xapp-"):
            SlackAdapter(config, mock_on_message)

    def test_init_extracts_default_agent(self, mock_on_message):
        """Should extract default_agent from config."""
        config = make_slack_config(default_agent="custom-agent")
        adapter = SlackAdapter(config, mock_on_message)

        assert adapter._default_agent == "custom-agent"

    def test_init_with_default_agent_fallback(self, mock_on_message):
        """Should default to 'ari' if no default_agent in config."""
        config = SourceConfig(
            source_id="test",
            source_type="slack",
            name="Test",
            config={},  # No default_agent
            credentials={
                "bot_token": "xoxb-test",
                "app_token": "xapp-test",
            },
        )
        adapter = SlackAdapter(config, mock_on_message)

        assert adapter._default_agent == "ari"

    def test_init_stores_credentials(self, slack_config, mock_on_message):
        """Should store bot_token and app_token."""
        adapter = SlackAdapter(slack_config, mock_on_message)

        assert adapter._bot_token == "xoxb-test-token-123"
        assert adapter._app_token == "xapp-test-app-token-456"

    def test_init_initializes_state(self, slack_config, mock_on_message):
        """Should initialize state variables."""
        adapter = SlackAdapter(slack_config, mock_on_message)

        assert adapter._app is None
        assert adapter._handler is None
        assert adapter._workspace_id is None
        assert adapter._workspace_name is None
        assert adapter.status == SourceStatus.STOPPED


# ==================== Start Tests ====================


class TestSlackAdapterStart:
    """Tests for SlackAdapter start lifecycle."""

    @pytest.mark.asyncio
    async def test_start_creates_app(
        self, slack_config, mock_on_message, mock_slack_app
    ):
        """start() should create App with bot_token."""
        with patch("daemon.sources.adapters.slack.adapter.AsyncApp", return_value=mock_slack_app) as mock_app_class:
            adapter = SlackAdapter(slack_config, mock_on_message)

            # Mock authentication
            adapter._authenticate = AsyncMock()

            # Mock handler creation
            mock_handler = MagicMock()
            mock_handler.start_async = AsyncMock()
            with patch(
                "daemon.sources.adapters.slack.adapter.AsyncSocketModeHandler",
                return_value=mock_handler
            ):
                await adapter.start()

            mock_app_class.assert_called_once_with(token="xoxb-test-token-123")

    @pytest.mark.asyncio
    async def test_start_registers_event_handlers(
        self, slack_config, mock_on_message
    ):
        """Should register message, app_mention, and /new command handlers."""
        adapter = SlackAdapter(slack_config, mock_on_message)
        adapter._authenticate = AsyncMock()

        # Track registered handlers
        registered_events = []
        registered_commands = []

        def capture_event_handler(*args, **kwargs):
            """Capture the event handler registration."""
            def decorator(func):
                registered_events.append((args, func))
                return func
            return decorator

        def capture_command_handler(*args, **kwargs):
            """Capture the command handler registration."""
            def decorator(func):
                registered_commands.append(func)
                return func
            return decorator

        mock_app = MagicMock()
        mock_app.event = MagicMock(side_effect=capture_event_handler)
        mock_app.command = MagicMock(side_effect=capture_command_handler)

        # Mock handler creation
        mock_handler = MagicMock()
        mock_handler.start_async = AsyncMock()
        with patch(
            "daemon.sources.adapters.slack.adapter.AsyncSocketModeHandler",
            return_value=mock_handler
        ):
            with patch(
                "daemon.sources.adapters.slack.adapter.AsyncApp",
                return_value=mock_app
            ):
                await adapter.start()

        # Verify message and app_mention events are both registered
        registered_event_types = [args[0] for args, _ in registered_events]
        assert "message" in registered_event_types
        assert "app_mention" in registered_event_types
        mock_app.command.assert_called_with("/new")

    @pytest.mark.asyncio
    async def test_start_authenticates(
        self, slack_config, mock_on_message, mock_slack_app
    ):
        """Should call _authenticate() and get workspace info."""
        adapter = SlackAdapter(slack_config, mock_on_message)
        adapter._authenticate = AsyncMock()

        # Mock handler creation
        mock_handler = MagicMock()
        mock_handler.start_async = AsyncMock()
        with patch(
            "daemon.sources.adapters.slack.adapter.AsyncSocketModeHandler",
            return_value=mock_handler
        ):
            await adapter.start()

        adapter._authenticate.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_starts_socket_mode_handler(
        self, slack_config, mock_on_message
    ):
        """Should create AsyncSocketModeHandler."""
        adapter = SlackAdapter(slack_config, mock_on_message)
        adapter._authenticate = AsyncMock()

        mock_handler_instance = MagicMock()
        mock_handler_instance.start_async = AsyncMock()

        handler_instances = []

        def create_handler(app, app_token):
            handler_instances.append((app, app_token))
            return mock_handler_instance

        with patch(
            "daemon.sources.adapters.slack.adapter.AsyncSocketModeHandler",
            side_effect=create_handler
        ):
            await adapter.start()

        # Verify handler was created with correct arguments
        assert len(handler_instances) == 1
        assert handler_instances[0][1] == "xapp-test-app-token-456"
        mock_handler_instance.start_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_sets_running_status(
        self, slack_config, mock_on_message, mock_slack_app
    ):
        """Status should be RUNNING after successful start."""
        adapter = SlackAdapter(slack_config, mock_on_message)
        adapter._authenticate = AsyncMock()

        mock_handler = MagicMock()
        mock_handler.start_async = AsyncMock()
        with patch(
            "daemon.sources.adapters.slack.adapter.AsyncSocketModeHandler",
            return_value=mock_handler
        ):
            await adapter.start()

        assert adapter.status == SourceStatus.RUNNING

    @pytest.mark.asyncio
    async def test_start_handles_auth_failure(
        self, slack_config, mock_on_message, mock_slack_app
    ):
        """Should set ERROR status if authentication fails."""
        adapter = SlackAdapter(slack_config, mock_on_message)
        adapter._authenticate = AsyncMock(
            side_effect=SlackAPIError("Authentication failed")
        )

        with patch(
            "daemon.sources.adapters.slack.adapter.AsyncSocketModeHandler"
        ):
            with pytest.raises(SlackAPIError):
                await adapter.start()

        assert adapter.status == SourceStatus.ERROR
        assert "Authentication failed" in adapter.error

    @pytest.mark.asyncio
    async def test_start_idempotent(self, slack_config, mock_on_message):
        """Starting when already running should be no-op."""
        adapter = SlackAdapter(slack_config, mock_on_message)
        adapter._status = SourceStatus.RUNNING

        await adapter.start()

        # Should not raise and status should remain RUNNING
        assert adapter.status == SourceStatus.RUNNING


# ==================== Stop Tests ====================


class TestSlackAdapterStop:
    """Tests for SlackAdapter stop lifecycle."""

    @pytest.mark.asyncio
    async def test_stop_closes_handler(self, mock_slack_adapter, mock_socket_mode_handler):
        """Should close the socket mode handler."""
        mock_slack_adapter._handler = mock_socket_mode_handler
        mock_slack_adapter._status = SourceStatus.RUNNING

        await mock_slack_adapter.stop()

        mock_socket_mode_handler.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_clears_app(self, mock_slack_adapter, mock_socket_mode_handler):
        """Should set _app to None."""
        mock_slack_adapter._handler = mock_socket_mode_handler
        mock_slack_adapter._app = MagicMock()
        mock_slack_adapter._status = SourceStatus.RUNNING

        await mock_slack_adapter.stop()

        assert mock_slack_adapter._app is None

    @pytest.mark.asyncio
    async def test_stop_sets_stopped_status(self, mock_slack_adapter, mock_socket_mode_handler):
        """Status should be STOPPED after stop()."""
        mock_slack_adapter._handler = mock_socket_mode_handler
        mock_slack_adapter._status = SourceStatus.RUNNING

        await mock_slack_adapter.stop()

        assert mock_slack_adapter.status == SourceStatus.STOPPED


# ==================== Send Tests (DB Lookup) ====================


class TestSlackAdapterSend:
    """Tests for SlackAdapter send functionality - DB lookup critical."""

    @pytest.mark.asyncio
    async def test_send_requires_running_status(self, mock_slack_adapter):
        """Should return False if not running."""
        mock_slack_adapter._status = SourceStatus.STOPPED

        message = OutgoingMessage(
            external_user_id="T123456:U123456",
            content="Hello",
            source_id="slack-main",
        )

        result = await mock_slack_adapter.send(message)
        assert result is False

    @pytest.mark.asyncio
    async def test_send_requires_source_repo(self, slack_config, mock_on_message):
        """Should return False if _source_repo not set."""
        adapter = SlackAdapter(slack_config, mock_on_message)
        adapter._status = SourceStatus.RUNNING
        adapter._workspace_id = "T123456"
        # _source_repo not set

        message = OutgoingMessage(
            external_user_id="T123456:U123456",
            content="Hello",
            source_id="slack-main",
        )

        result = await adapter.send(message)
        assert result is False

    @pytest.mark.asyncio
    async def test_send_returns_false_for_invalid_external_user_id(
        self, mock_slack_adapter
    ):
        """Invalid format should return False."""
        mock_slack_adapter._status = SourceStatus.RUNNING

        # Format with only one part (missing second part)
        message = OutgoingMessage(
            external_user_id="workspace_only",
            content="Hello",
            source_id="slack-main",
        )

        result = await mock_slack_adapter.send(message)
        assert result is False

    @pytest.mark.asyncio
    async def test_send_returns_false_when_no_mapping_found(
        self, mock_slack_adapter, mock_source_repo
    ):
        """DB lookup returns None -> False."""
        mock_slack_adapter._status = SourceStatus.RUNNING
        mock_source_repo.get_instance_mapping = MagicMock(return_value=None)
        mock_slack_adapter._source_repo = mock_source_repo

        message = OutgoingMessage(
            external_user_id="T123456:U123456",
            content="Hello",
            source_id="slack-main",
        )

        result = await mock_slack_adapter.send(message)
        assert result is False

    @pytest.mark.asyncio
    async def test_send_returns_false_when_no_channel_in_metadata(
        self, mock_slack_adapter, mock_source_repo
    ):
        """Mapping without slack_channel_id -> False."""
        mock_slack_adapter._status = SourceStatus.RUNNING

        # Mapping without slack_channel_id
        mock_mapping = MagicMock()
        mock_mapping.mapping_metadata = {}  # No slack_channel_id
        mock_source_repo.get_instance_mapping = MagicMock(return_value=mock_mapping)
        mock_slack_adapter._source_repo = mock_source_repo

        message = OutgoingMessage(
            external_user_id="T123456:U123456",
            content="Hello",
            source_id="slack-main",
        )

        result = await mock_slack_adapter.send(message)
        assert result is False

    @pytest.mark.asyncio
    async def test_send_success_with_valid_mapping(
        self, mock_slack_adapter, mock_source_repo
    ):
        """Successful send returns True."""
        mock_slack_adapter._status = SourceStatus.RUNNING
        mock_slack_adapter._app = MagicMock()

        # Valid mapping
        mock_mapping = MagicMock()
        mock_mapping.mapping_metadata = {
            "slack_channel_id": "C123456",
            "slack_thread_ts": None,
        }
        mock_source_repo.get_instance_mapping = MagicMock(return_value=mock_mapping)
        mock_slack_adapter._source_repo = mock_source_repo

        # Mock the safe API call
        mock_slack_adapter._safe_api_call = AsyncMock(return_value=(True, {}))

        message = OutgoingMessage(
            external_user_id="T123456:U123456",
            content="Hello World",
            source_id="slack-main",
        )

        result = await mock_slack_adapter.send(message)
        assert result is True

    @pytest.mark.asyncio
    async def test_send_uses_db_lookup_not_metadata(
        self, mock_slack_adapter, mock_source_repo
    ):
        """Verify _source_repo.get_instance_mapping is called (not metadata)."""
        mock_slack_adapter._status = SourceStatus.RUNNING
        mock_slack_adapter._app = MagicMock()

        # Valid mapping
        mock_mapping = MagicMock()
        mock_mapping.mapping_metadata = {
            "slack_channel_id": "C123456",
        }
        mock_source_repo.get_instance_mapping = MagicMock(return_value=mock_mapping)
        mock_slack_adapter._source_repo = mock_source_repo

        # Mock the safe API call
        mock_slack_adapter._safe_api_call = AsyncMock(return_value=(True, {}))

        message = OutgoingMessage(
            external_user_id="T123456:U123456",
            content="Hello",
            source_id="slack-main",
            metadata={"some": "data"},  # This should NOT be used
        )

        await mock_slack_adapter.send(message)

        # CRITICAL: Verify DB lookup was called
        mock_source_repo.get_instance_mapping.assert_called_once_with(
            "slack-main", "T123456:U123456"
        )

    @pytest.mark.asyncio
    async def test_send_uses_thread_ts_from_mapping(
        self, mock_slack_adapter, mock_source_repo
    ):
        """Should use slack_thread_ts from mapping metadata."""
        mock_slack_adapter._status = SourceStatus.RUNNING
        mock_slack_adapter._app = MagicMock()

        # Mapping with thread_ts
        mock_mapping = MagicMock()
        mock_mapping.mapping_metadata = {
            "slack_channel_id": "C123456",
            "slack_thread_ts": "1234567890.123456",
        }
        mock_source_repo.get_instance_mapping = MagicMock(return_value=mock_mapping)
        mock_slack_adapter._source_repo = mock_source_repo

        captured_params = {}

        async def capture_params(*args, **kwargs):
            captured_params.update(kwargs)
            return True, {}

        mock_slack_adapter._safe_api_call = AsyncMock(side_effect=capture_params)

        message = OutgoingMessage(
            external_user_id="T123456:C123456",  # Channel format
            content="Hello in thread",
            source_id="slack-main",
        )

        await mock_slack_adapter.send(message)

        # Verify thread_ts from mapping was used
        assert captured_params.get("thread_ts") == "1234567890.123456"

    @pytest.mark.asyncio
    async def test_short_message_applies_mrkdwn_formatting(
        self, mock_slack_adapter, mock_source_repo
    ):
        """Short messages with markdown should be converted to Slack mrkdwn."""
        mock_slack_adapter._status = SourceStatus.RUNNING
        mock_slack_adapter._app = MagicMock()

        mock_mapping = MagicMock()
        mock_mapping.mapping_metadata = {
            "slack_channel_id": "C123456",
        }
        mock_source_repo.get_instance_mapping = MagicMock(return_value=mock_mapping)
        mock_slack_adapter._source_repo = mock_source_repo

        captured_params = {}

        async def capture_params(*args, **kwargs):
            captured_params.update(kwargs)
            return True, {}

        mock_slack_adapter._safe_api_call = AsyncMock(side_effect=capture_params)

        message = OutgoingMessage(
            external_user_id="T123456:U123456",
            content="**bold text** and *italic*",
            source_id="slack-main",
        )

        await mock_slack_adapter.send(message)

        # Should send via the "text" param (short path), and the text should
        # be converted to Slack mrkdwn — NOT raw markdown.
        assert "blocks" not in captured_params
        assert captured_params.get("text") == "*bold text* and _italic_"
        assert "**" not in captured_params.get("text", "")

    @pytest.mark.asyncio
    async def test_short_message_plain_text_unchanged(
        self, mock_slack_adapter, mock_source_repo
    ):
        """Short messages without markdown should be sent verbatim."""
        mock_slack_adapter._status = SourceStatus.RUNNING
        mock_slack_adapter._app = MagicMock()

        mock_mapping = MagicMock()
        mock_mapping.mapping_metadata = {
            "slack_channel_id": "C123456",
        }
        mock_source_repo.get_instance_mapping = MagicMock(return_value=mock_mapping)
        mock_slack_adapter._source_repo = mock_source_repo

        captured_params = {}

        async def capture_params(*args, **kwargs):
            captured_params.update(kwargs)
            return True, {}

        mock_slack_adapter._safe_api_call = AsyncMock(side_effect=capture_params)

        message = OutgoingMessage(
            external_user_id="T123456:U123456",
            content="Hello World",
            source_id="slack-main",
        )

        await mock_slack_adapter.send(message)

        # Plain text should pass through the formatter unchanged.
        assert "blocks" not in captured_params
        assert captured_params.get("text") == "Hello World"

    @pytest.mark.asyncio
    async def test_fallback_to_simple_text_applies_mrkdwn_formatting(
        self, mock_slack_adapter, mock_source_repo
    ):
        """Fallback-to-text path (when blocks builder returns empty) must format."""
        mock_slack_adapter._status = SourceStatus.RUNNING
        mock_slack_adapter._app = MagicMock()

        mock_mapping = MagicMock()
        mock_mapping.mapping_metadata = {
            "slack_channel_id": "C123456",
        }
        mock_source_repo.get_instance_mapping = MagicMock(return_value=mock_mapping)
        mock_slack_adapter._source_repo = mock_source_repo

        # Force the long-content branch by making content > 400 chars, then
        # force the fallback-to-simple-text branch by having the blocks
        # builder return empty.
        long_content = "**bold lead** " + ("x" * 500)

        with patch(
            "daemon.sources.adapters.slack.adapter.markdown_to_slack_blocks",
            return_value=[],
        ):
            captured_params = {}

            async def capture_params(*args, **kwargs):
                captured_params.update(kwargs)
                return True, {}

            mock_slack_adapter._safe_api_call = AsyncMock(side_effect=capture_params)

            message = OutgoingMessage(
                external_user_id="T123456:U123456",
                content=long_content,
                source_id="slack-main",
            )

            await mock_slack_adapter.send(message)

        # Should fall back to text-only path and format the markdown.
        assert "blocks" not in captured_params
        sent_text = captured_params.get("text", "")
        assert "**" not in sent_text
        assert "*bold lead*" in sent_text


# ==================== _process_event Tests ====================


class TestSlackAdapterProcessEvent:
    """Tests for _process_event method."""

    @pytest.mark.asyncio
    async def test_process_event_dm_message(self, mock_slack_adapter):
        """DM should create IncomingMessage with correct external_user_id."""
        event = {
            "channel": "D123456",
            "channel_type": "im",
            "user": "U654321",
            "text": "Hello DM",
            "ts": "1234567890.123456",
        }

        result = await mock_slack_adapter._process_event(event)

        assert result is not None
        assert isinstance(result, IncomingMessage)
        assert result.external_user_id == "T123456:U654321"
        assert result.content == "Hello DM"
        assert result.message_type == "text"

    @pytest.mark.asyncio
    async def test_process_event_channel_message(self, mock_slack_adapter):
        """Channel message should use channel format."""
        event = {
            "channel": "C123456",
            "channel_type": "channel",
            "user": "U654321",
            "text": "Hello channel",
            "ts": "1234567890.123456",
        }

        result = await mock_slack_adapter._process_event(event)

        assert result is not None
        assert result.external_user_id == "T123456:C123456"
        assert result.content == "Hello channel"

    @pytest.mark.asyncio
    async def test_process_event_thread_message(self, mock_slack_adapter):
        """Thread should include thread_ts in external_user_id."""
        event = {
            "channel": "C123456",
            "channel_type": "channel",
            "user": "U654321",
            "text": "Hello thread",
            "ts": "1234567890.999999",
            "thread_ts": "1234567890.123456",
        }

        result = await mock_slack_adapter._process_event(event)

        assert result is not None
        assert result.external_user_id == "T123456:C123456:1234567890.123456"
        assert result.content == "Hello thread"

    @pytest.mark.asyncio
    async def test_process_event_skips_bot_messages(self, mock_slack_adapter):
        """bot_id present -> _is_valid_message returns False."""
        # Test that _is_valid_message correctly filters bot messages
        event = {
            "channel": "C123456",
            "channel_type": "channel",
            "user": "U654321",
            "text": "Bot message",
            "ts": "1234567890.123456",
            "bot_id": "B123456",  # Bot message
        }

        # _is_valid_message should return False for bot messages
        result = mock_slack_adapter._is_valid_message(event)
        assert result is False

        # _process_event itself doesn't filter - filtering happens in _handle_message_event
        # So _process_event will still create a message (but it would be filtered by caller)
        processed = await mock_slack_adapter._process_event(event)
        # Since filtering happens upstream, _process_event processes it
        assert processed is not None

    @pytest.mark.asyncio
    async def test_process_event_skips_own_messages(self, mock_slack_adapter):
        """user == bot_user_id -> _is_valid_message returns False."""
        # Test that _is_valid_message correctly filters own messages
        event = {
            "channel": "C123456",
            "channel_type": "channel",
            "user": "U123456",  # Same as _bot_user_id
            "text": "Own message",
            "ts": "1234567890.123456",
        }

        # _is_valid_message should return False for own messages
        result = mock_slack_adapter._is_valid_message(event)
        assert result is False

        # _process_event itself doesn't filter - filtering happens in _handle_message_event
        processed = await mock_slack_adapter._process_event(event)
        assert processed is not None

    @pytest.mark.asyncio
    async def test_process_event_handles_new_command(self, mock_slack_adapter):
        """/new command sets message_type to command."""
        event = {
            "channel": "C123456",
            "channel_type": "channel",
            "user": "U654321",
            "text": "/new start a task",
            "ts": "1234567890.123456",
        }

        result = await mock_slack_adapter._process_event(event)

        assert result is not None
        assert result.message_type == "command"
        assert result.metadata.get("force_new_instance") is True
        assert result.metadata.get("command") == "/new"

    @pytest.mark.asyncio
    async def test_process_event_handles_file_attachment(self, mock_slack_adapter):
        """Files should set text to '[File attached]'."""
        event = {
            "channel": "C123456",
            "channel_type": "channel",
            "user": "U654321",
            "text": "",  # No text
            "ts": "1234567890.123456",
            "files": [{"id": "F123456", "name": "document.pdf"}],
        }

        result = await mock_slack_adapter._process_event(event)

        assert result is not None
        assert result.content == "[File attached]"

    @pytest.mark.asyncio
    async def test_process_event_sets_correct_metadata(self, mock_slack_adapter):
        """Verify all metadata fields are set."""
        event = {
            "channel": "C123456",
            "channel_type": "channel",
            "user": "U654321",
            "text": "Test message",
            "ts": "1234567890.123456",
            "thread_ts": "1234567890.999999",
        }

        result = await mock_slack_adapter._process_event(event)

        assert result is not None
        assert result.metadata["slack"]["channel_id"] == "C123456"
        assert result.metadata["slack"]["channel_type"] == "channel"
        assert result.metadata["slack"]["user_id"] == "U654321"
        assert result.metadata["slack"]["ts"] == "1234567890.123456"
        assert result.metadata["slack"]["thread_ts"] == "1234567890.999999"
        assert result.metadata["slack"]["workspace_id"] == "T123456"
        assert result.metadata["slack"]["workspace_name"] == "Test Workspace"
        assert result.metadata["agent"] == "ari"
        assert result.metadata["reply_chat_id"] == "C123456"

    @pytest.mark.asyncio
    async def test_process_event_returns_none_for_empty_event(self, mock_slack_adapter):
        """Event without text or files returns None."""
        event = {
            "channel": "C123456",
            "channel_type": "channel",
            "user": "U654321",
            "ts": "1234567890.123456",
            # No text, no files
        }

        result = await mock_slack_adapter._process_event(event)

        assert result is None


# ==================== Health Check Tests ====================


class TestSlackAdapterHealthCheck:
    """Tests for health_check method."""

    @pytest.mark.asyncio
    async def test_health_check_returns_false_when_not_running(
        self, mock_slack_adapter
    ):
        """Should return False if status != RUNNING."""
        mock_slack_adapter._status = SourceStatus.STOPPED

        result = await mock_slack_adapter.health_check()

        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_calls_auth_test(self, mock_slack_adapter):
        """Should call auth.test API."""
        mock_slack_adapter._status = SourceStatus.RUNNING
        mock_slack_adapter._call_slack_api = AsyncMock(
            return_value={"ok": True}
        )

        await mock_slack_adapter.health_check()

        mock_slack_adapter._call_slack_api.assert_called_once_with("auth.test")

    @pytest.mark.asyncio
    async def test_health_check_returns_result(self, mock_slack_adapter):
        """Should return True if auth.test succeeds."""
        mock_slack_adapter._status = SourceStatus.RUNNING
        mock_slack_adapter._call_slack_api = AsyncMock(
            return_value={"ok": True}
        )

        result = await mock_slack_adapter.health_check()

        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_exception(self, mock_slack_adapter):
        """Should return False on exception."""
        mock_slack_adapter._status = SourceStatus.RUNNING
        mock_slack_adapter._call_slack_api = AsyncMock(
            side_effect=Exception("Network error")
        )

        result = await mock_slack_adapter.health_check()

        assert result is False


# ==================== Test Connection Tests ====================


class TestSlackAdapterTestConnection:
    """Tests for test_connection class method."""

    @pytest.mark.asyncio
    async def test_connection_requires_bot_token(self):
        """Should return False if missing."""
        config = SourceConfig(
            source_id="test",
            source_type="slack",
            name="Test",
            config={},
            credentials={"app_token": "xapp-test"},  # No bot_token
        )

        success, message = await SlackAdapter.test_connection(config)

        assert success is False
        assert "bot_token" in message.lower()

    @pytest.mark.asyncio
    async def test_connection_requires_app_token(self):
        """Should return False if missing."""
        config = SourceConfig(
            source_id="test",
            source_type="slack",
            name="Test",
            config={},
            credentials={"bot_token": "xoxb-test"},  # No app_token
        )

        success, message = await SlackAdapter.test_connection(config)

        assert success is False
        assert "app_token" in message.lower()

    @pytest.mark.asyncio
    async def test_connection_validates_token_format(self):
        """Should validate xoxb- and xapp- prefixes."""
        config = SourceConfig(
            source_id="test",
            source_type="slack",
            name="Test",
            config={},
            credentials={
                "bot_token": "invalid-bot",
                "app_token": "invalid-app",
            },
        )

        success, message = await SlackAdapter.test_connection(config)

        assert success is False
        assert "xoxb-" in message or "xapp-" in message

    @pytest.mark.asyncio
    async def test_connection_success(self):
        """Should return True with workspace info."""
        config = make_slack_config()

        # Create proper async context manager mock
        mock_response = MagicMock()
        mock_response.json = AsyncMock(
            return_value={
                "ok": True,
                "team": "Test Workspace",
                "user": "test-bot",
            }
        )

        mock_session = MagicMock()
        mock_session.get = MagicMock(
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=mock_response),
                __aexit__=AsyncMock(return_value=None),
            )
        )

        # Mock the ClientSession constructor
        mock_session_instance = MagicMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)
        mock_session_instance.get = MagicMock(
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=mock_response),
                __aexit__=AsyncMock(return_value=None),
            )
        )

        with patch("aiohttp.ClientSession", return_value=mock_session_instance):
            success, message = await SlackAdapter.test_connection(config)

        assert success is True
        assert "Test Workspace" in message
        assert "test-bot" in message

    @pytest.mark.asyncio
    async def test_connection_invalid_auth(self):
        """Should return False for invalid token."""
        config = make_slack_config()

        mock_response = MagicMock()
        mock_response.json = AsyncMock(
            return_value={
                "ok": False,
                "error": "invalid_auth",
            }
        )

        mock_session_instance = MagicMock()
        mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
        mock_session_instance.__aexit__ = AsyncMock(return_value=None)
        mock_session_instance.get = MagicMock(
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=mock_response),
                __aexit__=AsyncMock(return_value=None),
            )
        )

        with patch("aiohttp.ClientSession", return_value=mock_session_instance):
            success, message = await SlackAdapter.test_connection(config)

        assert success is False
        assert "invalid" in message.lower() or "token" in message.lower()


# ==================== Authentication Tests ====================


class TestSlackAdapterAuthenticate:
    """Tests for _authenticate method."""

    @pytest.mark.asyncio
    async def test_authenticate_sets_workspace_info(self, mock_slack_adapter):
        """Should set workspace_id, workspace_name, bot_user_id, bot_name."""
        mock_slack_adapter._call_slack_api = AsyncMock(
            return_value={
                "ok": True,
                "team_id": "T_WS123",
                "team": "My Workspace",
                "user_id": "U_BOT789",
                "user": "my-bot",
            }
        )

        await mock_slack_adapter._authenticate()

        assert mock_slack_adapter._workspace_id == "T_WS123"
        assert mock_slack_adapter._workspace_name == "My Workspace"
        assert mock_slack_adapter._bot_user_id == "U_BOT789"
        assert mock_slack_adapter._bot_name == "my-bot"

    @pytest.mark.asyncio
    async def test_authenticate_raises_on_failure(self, mock_slack_adapter):
        """Should raise SlackAPIError if auth fails."""
        mock_slack_adapter._call_slack_api = AsyncMock(
            return_value={"ok": False, "error": "invalid_auth"}
        )

        with pytest.raises(SlackAPIError, match="Authentication failed"):
            await mock_slack_adapter._authenticate()


# ==================== Build External User ID Tests ====================


class TestSlackAdapterBuildExternalUserId:
    """Tests for _build_external_user_id method."""

    def test_build_dm_external_user_id(self, mock_slack_adapter):
        """DM should return workspace:user_id format."""
        event = {
            "channel": "D123456",
            "channel_type": "im",
            "user": "U654321",
        }

        result = mock_slack_adapter._build_external_user_id(event)

        assert result == "T123456:U654321"

    def test_build_channel_external_user_id(self, mock_slack_adapter):
        """Channel should return workspace:channel_id format."""
        event = {
            "channel": "C123456",
            "channel_type": "channel",
            "user": "U654321",
        }

        result = mock_slack_adapter._build_external_user_id(event)

        assert result == "T123456:C123456"

    def test_build_thread_external_user_id(self, mock_slack_adapter):
        """Thread should return workspace:channel_id:thread_ts format."""
        event = {
            "channel": "C123456",
            "channel_type": "channel",
            "user": "U654321",
            "thread_ts": "1234567890.123456",
        }

        result = mock_slack_adapter._build_external_user_id(event)

        assert result == "T123456:C123456:1234567890.123456"

    def test_build_external_user_id_without_workspace(self, slack_config, mock_on_message):
        """Should return None if workspace_id not set."""
        adapter = SlackAdapter(slack_config, mock_on_message)
        # _workspace_id is None

        event = {
            "channel": "C123456",
            "channel_type": "channel",
            "user": "U654321",
        }

        result = adapter._build_external_user_id(event)

        assert result is None

    def test_build_dm_without_user(self, mock_slack_adapter):
        """DM without user should return None."""
        event = {
            "channel": "D123456",
            "channel_type": "im",
            # No user
        }

        result = mock_slack_adapter._build_external_user_id(event)

        assert result is None


# ==================== Is Valid Message Tests ====================


class TestSlackAdapterIsValidMessage:
    """Tests for _is_valid_message method."""

    def test_rejects_bot_message(self, mock_slack_adapter):
        """Should reject messages with bot_id."""
        event = {
            "bot_id": "B123456",
            "user": "U654321",
        }

        result = mock_slack_adapter._is_valid_message(event)

        assert result is False

    def test_rejects_bot_profile(self, mock_slack_adapter):
        """Should reject messages with bot_profile."""
        event = {
            "bot_profile": {"id": "B123456"},
            "user": "U654321",
        }

        result = mock_slack_adapter._is_valid_message(event)

        assert result is False

    def test_rejects_own_messages(self, mock_slack_adapter):
        """Should reject messages from bot's own user_id."""
        event = {
            "user": "U123456",  # Same as _bot_user_id
        }

        result = mock_slack_adapter._is_valid_message(event)

        assert result is False

    def test_rejects_channel_join(self, mock_slack_adapter):
        """Should reject channel_join subtype."""
        event = {
            "user": "U654321",
            "subtype": "channel_join",
        }

        result = mock_slack_adapter._is_valid_message(event)

        assert result is False

    def test_rejects_thread_broadcast(self, mock_slack_adapter):
        """Should reject thread_broadcast subtype."""
        event = {
            "user": "U654321",
            "subtype": "thread_broadcast",
        }

        result = mock_slack_adapter._is_valid_message(event)

        assert result is False

    def test_accepts_valid_message(self, mock_slack_adapter):
        """Should accept valid user message."""
        event = {
            "user": "U654321",
            "text": "Hello",
        }

        result = mock_slack_adapter._is_valid_message(event)

        assert result is True


# ==================== Channel Mention Filter Tests ====================


class TestChannelMentionFilter:
    """Tests for channel_require_mention config option."""

    def test_default_require_mention_is_true(self, slack_config, mock_on_message):
        """Default behavior requires mention in channels."""
        adapter = SlackAdapter(slack_config, mock_on_message)
        assert adapter._channel_require_mention is True

    def test_config_disables_mention_requirement(
        self, slack_config, mock_on_message
    ):
        """channel_require_mention=False disables the filter."""
        config = make_slack_config(channel_require_mention=False)
        adapter = SlackAdapter(config, mock_on_message)
        assert adapter._channel_require_mention is False

    def test_dm_message_always_passes(self, mock_slack_adapter):
        """DMs (channel_type=im) pass regardless of mention."""
        event = {
            "user": "U654321",
            "channel": "D999",
            "channel_type": "im",
            "text": "no mention here",
        }
        assert mock_slack_adapter._is_bot_mentioned(event) is True

    def test_mpim_message_always_passes(self, mock_slack_adapter):
        """Multi-party DMs always pass."""
        event = {
            "user": "U654321",
            "channel": "G999",
            "channel_type": "mpim",
            "text": "no mention here",
        }
        assert mock_slack_adapter._is_bot_mentioned(event) is True

    def test_channel_message_with_mention_passes(self, mock_slack_adapter):
        """Channel message containing <@BOTID> mention passes."""
        event = {
            "user": "U654321",
            "channel": "C123",
            "channel_type": "channel",
            "text": "<@U123456> hello there",
        }
        assert mock_slack_adapter._is_bot_mentioned(event) is True

    def test_channel_message_without_mention_blocked(self, mock_slack_adapter):
        """Channel message without mention is filtered out."""
        event = {
            "user": "U654321",
            "channel": "C123",
            "channel_type": "channel",
            "text": "just chatting, no mention",
        }
        assert mock_slack_adapter._is_bot_mentioned(event) is False

    def test_private_channel_without_mention_blocked(self, mock_slack_adapter):
        """Private channel message without mention is filtered out."""
        event = {
            "user": "U654321",
            "channel": "G123",
            "channel_type": "group",
            "text": "secret discussion",
        }
        assert mock_slack_adapter._is_bot_mentioned(event) is False

    def test_app_mention_event_always_passes(self, mock_slack_adapter):
        """app_mention events always pass the filter (by event type)."""
        event = {
            "type": "app_mention",
            "user": "U654321",
            "channel": "C123",
            "channel_type": "channel",
            "text": "no token in text somehow",
        }
        assert mock_slack_adapter._is_bot_mentioned(event) is True

    def test_fails_open_when_bot_user_id_unknown(self, mock_slack_adapter):
        """If bot_user_id is not yet known, fail open (don't drop messages)."""
        mock_slack_adapter._bot_user_id = None
        event = {
            "user": "U654321",
            "channel": "C123",
            "channel_type": "channel",
            "text": "some text",
        }
        assert mock_slack_adapter._is_bot_mentioned(event) is True

    def test_message_without_channel_type_treated_as_channel(
        self, mock_slack_adapter
    ):
        """Default channel_type is 'channel' — missing mention is blocked."""
        event = {
            "user": "U654321",
            "channel": "C123",
            "text": "no mention here",
        }
        assert mock_slack_adapter._is_bot_mentioned(event) is False

    @pytest.mark.asyncio
    async def test_handle_message_event_skips_unmentioned_channel(
        self, mock_slack_adapter, mock_on_message
    ):
        """End-to-end: channel message without mention is dropped before emit."""
        mock_slack_adapter._emit_message = AsyncMock()
        event = {
            "user": "U654321",
            "channel": "C123",
            "channel_type": "channel",
            "text": "no mention here",
        }
        await mock_slack_adapter._handle_message_event(event, client=MagicMock())
        mock_slack_adapter._emit_message.assert_not_called()
        mock_on_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_message_event_emits_mentioned_channel(
        self, mock_slack_adapter, mock_on_message
    ):
        """End-to-end: app_mention event for a channel flows through to emit."""
        mock_slack_adapter._emit_message = AsyncMock()
        event = {
            "type": "app_mention",  # Canonical event type for @-mentions
            "user": "U654321",
            "channel": "C123",
            "channel_type": "channel",
            "text": "<@U123456> hello",
        }
        await mock_slack_adapter._handle_message_event(event, client=MagicMock())
        mock_slack_adapter._emit_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_message_event_dedupes_app_mention(
        self, mock_slack_adapter, mock_on_message
    ):
        """When Slack fires BOTH 'message' and 'app_mention' for a mention,
        only the app_mention variant should be processed to avoid duplicate
        responses."""
        mock_slack_adapter._emit_message = AsyncMock()

        # The 'message' variant — should be deduplicated
        message_event = {
            "type": "message",
            "user": "U654321",
            "channel": "C123",
            "channel_type": "channel",
            "text": "<@U123456> hi",
        }
        await mock_slack_adapter._handle_message_event(
            message_event, client=MagicMock()
        )
        mock_slack_adapter._emit_message.assert_not_called()

        # The 'app_mention' variant — should be processed
        mention_event = {
            "type": "app_mention",
            "user": "U654321",
            "channel": "C123",
            "channel_type": "channel",
            "text": "<@U123456> hi",
        }
        await mock_slack_adapter._handle_message_event(
            mention_event, client=MagicMock()
        )
        mock_slack_adapter._emit_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_message_event_no_dedup_in_dm(
        self, mock_slack_adapter, mock_on_message
    ):
        """In DMs, only the 'message' event fires (not app_mention),
        so we process it normally without dedup logic."""
        mock_slack_adapter._emit_message = AsyncMock()
        event = {
            "type": "message",
            "user": "U654321",
            "channel": "D999",
            "channel_type": "im",
            "text": "no mention here",
        }
        await mock_slack_adapter._handle_message_event(event, client=MagicMock())
        mock_slack_adapter._emit_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_message_event_no_dedup_when_filter_off(
        self, slack_config, mock_on_message, mock_source_repo
    ):
        """With channel_require_mention=False, unmentioned 'message' events
        in channels should still pass through (no dedup triggered)."""
        config = make_slack_config(channel_require_mention=False)
        adapter = SlackAdapter(config, mock_on_message)
        adapter._source_repo = mock_source_repo
        adapter._workspace_id = "T123456"
        adapter._bot_user_id = "U123456"
        adapter._emit_message = AsyncMock()

        event = {
            "type": "message",  # Not an app_mention, but no mention either
            "user": "U654321",
            "channel": "C123",
            "channel_type": "channel",
            "text": "just chatting",
        }
        await adapter._handle_message_event(event, client=MagicMock())
        adapter._emit_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_message_event_emits_dm(
        self, mock_slack_adapter, mock_on_message
    ):
        """End-to-end: DM (no mention) still flows through."""
        mock_slack_adapter._emit_message = AsyncMock()
        event = {
            "user": "U654321",
            "channel": "D999",
            "channel_type": "im",
            "text": "no mention here",
        }
        await mock_slack_adapter._handle_message_event(event, client=MagicMock())
        mock_slack_adapter._emit_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_message_event_skips_when_filter_disabled_for_others(
        self, slack_config, mock_on_message, mock_source_repo
    ):
        """With channel_require_mention=False, channel messages still emit."""
        config = make_slack_config(channel_require_mention=False)
        adapter = SlackAdapter(config, mock_on_message)
        adapter._source_repo = mock_source_repo
        adapter._workspace_id = "T123456"
        adapter._bot_user_id = "U123456"
        adapter._emit_message = AsyncMock()

        event = {
            "user": "U654321",
            "channel": "C123",
            "channel_type": "channel",
            "text": "no mention here",
        }
        await adapter._handle_message_event(event, client=MagicMock())
        adapter._emit_message.assert_called_once()


# ==================== Text Cleaning Tests ====================


class TestSlackAdapterTextCleaning:
    """Tests for stripping Slack mention tokens and IDE prompt tags."""

    def test_strips_bot_mention_token(self, mock_slack_adapter):
        """Strips <@BOTID> from the start of the message."""
        text = "<@U123456> hi"
        result = mock_slack_adapter._clean_message_text(text)
        assert result == "hi"

    def test_strips_bot_mention_with_display_name(self, mock_slack_adapter):
        """Strips <@BOTID|display name> form."""
        text = "<@U123456|ensemble_bot> please help"
        result = mock_slack_adapter._clean_message_text(text)
        assert result == "please help"

    def test_strips_user_mention_mid_text(self, mock_slack_adapter):
        """Strips other user mentions anywhere in the text."""
        text = "hey <@U999> can you ask <@U123456> about this"
        result = mock_slack_adapter._clean_message_text(text)
        assert result == "hey can you ask about this"

    def test_strips_usergroup_mention(self, mock_slack_adapter):
        """Strips <!subteam^ID> mentions."""
        text = "<!subteam^S12345> heads up <@U123456> review this"
        result = mock_slack_adapter._clean_message_text(text)
        assert result == "heads up review this"

    def test_strips_here_and_channel_mentions(self, mock_slack_adapter):
        """Strips <!here> and <!channel> broadcast mentions."""
        text = "<!here> standup time"
        result = mock_slack_adapter._clean_message_text(text)
        assert result == "standup time"

    def test_strips_environment_details_block(self, mock_slack_adapter):
        """Strips leaked <environment_details>...</environment_details>."""
        text = "hi\n<environment_details>\nfoo\nbar\n</environment_details>"
        result = mock_slack_adapter._clean_message_text(text)
        assert result == "hi"

    def test_strips_environment_details_multiline(self, mock_slack_adapter):
        """Strips environment_details with multi-line content."""
        text = (
            "hello\n<environment_details>\n"
            "Current time: 2026-06-13T23:21:25+07:00\n"
            "Working directory: /foo\n"
            "</environment_details>"
        )
        result = mock_slack_adapter._clean_message_text(text)
        assert result == "hello"

    def test_preserves_plain_text(self, mock_slack_adapter):
        """Plain text without any tags is unchanged."""
        text = "just a normal message"
        result = mock_slack_adapter._clean_message_text(text)
        assert result == "just a normal message"

    def test_preserves_text_with_angle_brackets_not_mention(
        self, mock_slack_adapter
    ):
        """Angle brackets that aren't mention tokens are preserved."""
        text = "use 1 < 2 and 3 > 1"
        result = mock_slack_adapter._clean_message_text(text)
        assert result == "use 1 < 2 and 3 > 1"

    def test_empty_text_returns_empty(self, mock_slack_adapter):
        """Empty input returns empty."""
        assert mock_slack_adapter._clean_message_text("") == ""

    def test_handles_whitespace_collapse(self, mock_slack_adapter):
        """Collapses multiple spaces from removed tokens."""
        text = "<@U123456>    hello"
        result = mock_slack_adapter._clean_message_text(text)
        assert result == "hello"

    def test_process_event_uses_cleaned_text(self, mock_slack_adapter):
        """End-to-end: the emitted IncomingMessage has cleaned content."""
        import asyncio

        # Build a fully valid event that will pass _is_valid_message
        event = {
            "type": "app_mention",
            "user": "U654321",
            "channel": "C123",
            "channel_type": "channel",
            "text": "<@U123456> hello",
            "ts": "1234567890.123456",
        }
        incoming = asyncio.run(mock_slack_adapter._process_event(event))
        assert incoming is not None
        assert incoming.content == "hello"

    def test_process_event_preserves_plain_text(self, mock_slack_adapter):
        """End-to-end: plain text is preserved unchanged."""
        import asyncio

        event = {
            "type": "message",
            "user": "U654321",
            "channel": "D999",
            "channel_type": "im",
            "text": "what time is it?",
            "ts": "1234567890.123456",
        }
        incoming = asyncio.run(mock_slack_adapter._process_event(event))
        assert incoming is not None
        assert incoming.content == "what time is it?"


# ==================== Rate Limiter Tests ====================


class TestSlackAdapterRateLimiter:
    """Tests that rate limiter is properly initialized."""

    def test_rate_limiter_initialized(self, slack_config, mock_on_message):
        """Should initialize tiered rate limiter."""
        adapter = SlackAdapter(slack_config, mock_on_message)

        assert adapter._rate_limiter is not None


# ==================== Circuit Breaker Tests ====================


class TestSlackAdapterCircuitBreaker:
    """Tests for circuit breaker integration."""

    def test_circuit_breaker_initialized(self, slack_config, mock_on_message):
        """Should initialize circuit breaker with defaults."""
        adapter = SlackAdapter(slack_config, mock_on_message)

        assert adapter._circuit_breaker is not None
        assert adapter._circuit_breaker.failure_threshold == 5
        assert adapter._circuit_breaker.recovery_timeout == 60.0

    @pytest.mark.asyncio
    async def test_send_fails_when_circuit_open(self, mock_slack_adapter, mock_source_repo):
        """Should return False when circuit breaker is open."""
        mock_slack_adapter._status = SourceStatus.RUNNING

        # Set circuit breaker to open state
        for _ in range(5):
            await mock_slack_adapter._circuit_breaker.record_failure()

        assert await mock_slack_adapter._circuit_breaker.can_execute() is False

        message = OutgoingMessage(
            external_user_id="T123456:U123456",
            content="Hello",
            source_id="slack-main",
        )

        result = await mock_slack_adapter.send(message)
        assert result is False


# ==================== /new Command Handler Tests ====================


class TestSlackAdapterNewCommand:
    """Tests for _handle_new_command method."""

    @pytest.mark.asyncio
    async def test_new_command_awaits_ack(self, mock_slack_adapter):
        """ack() should be awaited in async slack-bolt."""
        ack_mock = AsyncMock()

        body = {
            "user_id": "U654321",
            "channel_id": "C123456",
            "team_id": "T111111",
            "text": "/new start a task",
            "user_name": "alice",
        }

        await mock_slack_adapter._handle_new_command(ack_mock, body, None)

        ack_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_new_command_missing_user_id(self, mock_slack_adapter):
        """Should return early if user_id is missing."""
        ack_mock = AsyncMock()

        body = {
            "channel_id": "C123456",
            "team_id": "T111111",
            "text": "/new task",
        }

        emit_called = []

        async def capture_emit(msg):
            emit_called.append(msg)

        mock_slack_adapter._emit_message = capture_emit

        await mock_slack_adapter._handle_new_command(ack_mock, body, None)

        ack_mock.assert_awaited_once()
        assert len(emit_called) == 0

    @pytest.mark.asyncio
    async def test_new_command_missing_channel_id(self, mock_slack_adapter):
        """Should return early if channel_id is missing."""
        ack_mock = AsyncMock()

        body = {
            "user_id": "U654321",
            "team_id": "T111111",
            "text": "/new task",
        }

        emit_called = []

        async def capture_emit(msg):
            emit_called.append(msg)

        mock_slack_adapter._emit_message = capture_emit

        await mock_slack_adapter._handle_new_command(ack_mock, body, None)

        ack_mock.assert_awaited_once()
        assert len(emit_called) == 0

    @pytest.mark.asyncio
    async def test_new_command_emits_with_correct_fields(self, mock_slack_adapter):
        """Emitted message should have correct fields and metadata."""
        ack_mock = AsyncMock()

        body = {
            "user_id": "U654321",
            "channel_id": "C123456",
            "team_id": "T111111",
            "text": "/new start a new task",
            "user_name": "alice",
        }

        emit_called = []

        async def capture_emit(msg):
            emit_called.append(msg)

        mock_slack_adapter._emit_message = capture_emit

        await mock_slack_adapter._handle_new_command(ack_mock, body, None)

        ack_mock.assert_awaited_once()
        assert len(emit_called) == 1

        msg = emit_called[0]
        assert msg.content == "/new start a new task"
        assert msg.message_type == "command"
        assert msg.external_user_id == "T111111:C123456"
        assert msg.metadata.get("force_new_instance") is True
        assert msg.metadata.get("command") == "/new"
        assert msg.metadata.get("agent") == "ari"
        assert msg.metadata["slack"]["channel_id"] == "C123456"
        assert msg.metadata["slack"]["workspace_id"] == "T111111"
        assert msg.metadata["slack"]["user_id"] == "U654321"
        assert msg.metadata["slack"]["user_name"] == "alice"

    @pytest.mark.asyncio
    async def test_new_command_empty_text_defaults_to_slash(self, mock_slack_adapter):
        """When text is empty, content should default to '/new'."""
        ack_mock = AsyncMock()

        body = {
            "user_id": "U654321",
            "channel_id": "C123456",
            "team_id": "T111111",
            "text": "",
            "user_name": "bob",
        }

        emit_called = []

        async def capture_emit(msg):
            emit_called.append(msg)

        mock_slack_adapter._emit_message = capture_emit

        await mock_slack_adapter._handle_new_command(ack_mock, body, None)

        assert len(emit_called) == 1
        assert emit_called[0].content == "/new"

    @pytest.mark.asyncio
    async def test_new_command_in_dm_uses_user_id_in_external_id(self, mock_slack_adapter):
        """In a DM (channel_id starts with 'D'), external_user_id must be
        {team}:{user_id} so it matches the chat path's {team}:{user_id}
        and /new actually resets the conversation the next chat message uses.
        Regression test for the stale-mapping bug where /new and chat
        lived in different mapping namespaces in a DM.
        """
        ack_mock = AsyncMock()

        body = {
            "user_id": "U0B82KVQC1W",
            "channel_id": "D0B78CA4LHY",  # DM channel (D prefix)
            "team_id": "T0B74VCARKP",
            "text": "/new",
            "user_name": "alice",
        }

        emit_called = []

        async def capture_emit(msg):
            emit_called.append(msg)

        mock_slack_adapter._emit_message = capture_emit

        await mock_slack_adapter._handle_new_command(ack_mock, body, None)

        assert len(emit_called) == 1
        # Must use user_id, NOT channel_id, so it matches the chat path.
        assert emit_called[0].external_user_id == "T0B74VCARKP:U0B82KVQC1W"

    @pytest.mark.asyncio
    async def test_new_command_in_channel_uses_channel_id(self, mock_slack_adapter):
        """In a regular channel (C/G prefix), external_user_id stays
        {team}:{channel_id} (no change from prior behavior).
        """
        ack_mock = AsyncMock()

        body = {
            "user_id": "U654321",
            "channel_id": "C123456",
            "team_id": "T111111",
            "text": "/new",
            "user_name": "alice",
        }

        emit_called = []

        async def capture_emit(msg):
            emit_called.append(msg)

        mock_slack_adapter._emit_message = capture_emit

        await mock_slack_adapter._handle_new_command(ack_mock, body, None)

        assert len(emit_called) == 1
        assert emit_called[0].external_user_id == "T111111:C123456"


# ==================== DM Cache Tests ====================


class TestSlackAdapterDMCache:
    """Tests for DM cache TTL behavior."""

    def test_dm_cache_max_size_constant(self, slack_config, mock_on_message):
        """Should have DM_CACHE_MAX_SIZE constant."""
        adapter = SlackAdapter(slack_config, mock_on_message)
        assert hasattr(adapter, "DM_CACHE_MAX_SIZE")
        assert adapter.DM_CACHE_MAX_SIZE == 1000

    def test_dm_cache_ttl_constant(self, slack_config, mock_on_message):
        """Should have DM_CACHE_TTL_SECONDS constant."""
        adapter = SlackAdapter(slack_config, mock_on_message)
        assert hasattr(adapter, "DM_CACHE_TTL_SECONDS")
        assert adapter.DM_CACHE_TTL_SECONDS == 300  # 5 minutes

    @pytest.mark.asyncio
    async def test_evict_expired_cache_entries(self, slack_config, mock_on_message):
        """_evict_expired_cache_entries should remove expired entries."""
        adapter = SlackAdapter(slack_config, mock_on_message)

        # Manually add cache entries with different timestamps
        now = time_module.monotonic()
        adapter._dm_cache["user1"] = ("channel1", now - 600)  # Expired (10 min ago)
        adapter._dm_cache["user2"] = ("channel2", now - 400)  # Expired (400 sec > 300 TTL)
        adapter._dm_cache["user3"] = ("channel3", now - 50)   # Not expired (50 sec ago)

        adapter._evict_expired_cache_entries(now)

        # Only user3 should remain
        assert "user1" not in adapter._dm_cache
        assert "user2" not in adapter._dm_cache
        assert "user3" in adapter._dm_cache

    @pytest.mark.asyncio
    async def test_cache_eviction_on_resolve(self, mock_slack_adapter):
        """Cache should evict expired entries before adding new one."""
        mock_slack_adapter._call_slack_api = AsyncMock(
            return_value={"channel": {"id": "D123456"}}
        )

        # Add expired entries to cache
        now = time_module.monotonic()
        mock_slack_adapter._dm_cache["expired_user"] = ("D_expired", now - 600)

        # Resolve a new channel
        result = await mock_slack_adapter._resolve_dm_channel("new_user")

        # Verify the new entry was added
        assert result == "D123456"
        assert "new_user" in mock_slack_adapter._dm_cache
        # Expired entry should have been evicted
        assert "expired_user" not in mock_slack_adapter._dm_cache

    @pytest.mark.asyncio
    async def test_cache_size_limit_enforced(self, mock_slack_adapter):
        """Cache should not exceed DM_CACHE_MAX_SIZE."""
        mock_slack_adapter._call_slack_api = AsyncMock(
            return_value={"channel": {"id": "D123456"}}
        )

        # Fill cache beyond max size
        now = time_module.monotonic()
        for i in range(1200):  # More than 1000
            mock_slack_adapter._dm_cache[f"user_{i}"] = (f"channel_{i}", now)

        # Resolve a new channel
        await mock_slack_adapter._resolve_dm_channel("new_user")

        # Cache size should be limited to max
        assert len(mock_slack_adapter._dm_cache) <= mock_slack_adapter.DM_CACHE_MAX_SIZE

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_channel(self, mock_slack_adapter):
        """Cache hit should return cached channel without API call."""
        now = time_module.monotonic()
        mock_slack_adapter._dm_cache["U123456"] = ("D_cached", now - 10)  # Recent

        # Mock _call_slack_api to verify it's NOT called
        mock_slack_adapter._call_slack_api = AsyncMock()

        result = await mock_slack_adapter._resolve_dm_channel("U123456")

        assert result == "D_cached"
        # API should NOT be called for cache hit
        mock_slack_adapter._call_slack_api.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_miss_calls_api(self, mock_slack_adapter):
        """Cache miss should call API to resolve channel."""
        mock_slack_adapter._call_slack_api = AsyncMock(
            return_value={"channel": {"id": "D_new"}}
        )

        # Don't have in cache
        result = await mock_slack_adapter._resolve_dm_channel("U_new")

        assert result == "D_new"
        mock_slack_adapter._call_slack_api.assert_called_once_with(
            "conversations.open", users=["U_new"]
        )


# ============================================================================
# Phase B — chart-image delivery tests (Phase B.5 tasks #36-#39, #46, #47)
# ============================================================================
#
# Implements:
#   * test_slack_single_files_upload_v2 (Task #22 / amendment #9)
#   * test_slack_capability_classified_before_record (Task #20 / amendment #4)
#   * test_slack_capability_flag_zero_api_calls (Task #21 / amendment #4)
#   * test_slack_warn_once_per_channel (Task #24 / amendment #4)
#   * test_slack_initial_comment_followup (Task #23 / amendment #8)
#   * test_slack_multi_image_no_silent_drop (Task #47 / iter-003 blocking #2)


import base64

from daemon.sources.adapters.slack.adapter import SlackCapabilityError
from daemon.sources.base import ImageAttachment


def _make_slack_att(image_id: str = "a" * 32, *, size: int = 100) -> ImageAttachment:
    bytes_b64 = base64.b64encode(b"X" * size).decode("ascii")
    return ImageAttachment(
        image_id=image_id,
        content_type="image/png",
        filename=f"chart-{image_id[:8]}.png",
        size_bytes=size,
        bytes_b64=bytes_b64,
    )


# --- transport-layer stub for real-AsyncWebClient files_upload_v2 tests ---
# AsyncWebClient.files_upload_v2 (and every SDK method above it) stays real;
# only the HTTP request/response boundary is faked. This is the seam the
# channel= vs channel_id= kwarg-drift test guards (tester VERIFICATION
# 2026-10-04 finding #1).


class _FakeSlackHttpResponse:
    """Minimal aiohttp.ClientResponse stand-in (transport layer only)."""

    def __init__(self, payload, status: int = 200, content_type: str = "application/json"):
        self._payload = payload
        self.status = status
        self.headers: dict = {}
        self.content_type = content_type

    async def json(self):
        return self._payload

    async def text(self):
        return self._payload if isinstance(self._payload, str) else json.dumps(self._payload)

    async def read(self):
        return self._payload if isinstance(self._payload, bytes) else self._payload.encode()


class _FakeSlackResponseContext:
    """async-with wrapper so ``async with session.request(...)`` yields a response."""

    def __init__(self, response: _FakeSlackHttpResponse):
        self._response = response

    async def __aenter__(self) -> _FakeSlackHttpResponse:
        return self._response

    async def __aexit__(self, *exc) -> bool:
        return False


class _FakeSlackHttpSession:
    """aiohttp.ClientSession stand-in — records every request; zero network.

    Routes canned responses per endpoint so the full v2 chain
    (files.getUploadURLExternal -> binary upload POST -> files.completeUploadExternal)
    + chat.postMessage all complete hermetically.
    """

    def __init__(self):
        self.requests: list[dict] = []

    @property
    def closed(self) -> bool:
        return False

    def request(self, http_verb: str, api_url: str, **req_args):
        self.requests.append({"verb": http_verb, "url": api_url, "req_args": req_args})
        return _FakeSlackResponseContext(self._route(api_url))

    @staticmethod
    def _route(api_url: str) -> _FakeSlackHttpResponse:
        if api_url.endswith("files.getUploadURLExternal"):
            return _FakeSlackHttpResponse(
                {"ok": True, "file_id": "FTEST0001", "upload_url": "https://files.slack.com/upload/v1/T0TEST"}
            )
        if api_url.endswith("files.completeUploadExternal"):
            return _FakeSlackHttpResponse({"ok": True, "files": [{"id": "FTEST0001"}]})
        if "files.slack.com/upload/" in api_url:
            # step-2 binary PUT answers text/plain ("OK") like the real endpoint
            return _FakeSlackHttpResponse("OK", content_type="text/plain")
        return _FakeSlackHttpResponse({"ok": True})


@pytest.fixture
def real_slack_sdk():
    """Swap the conftest-injected ``slack_sdk`` mock for the real package (per-test).

    Mirrors ``tests/e2e/conftest.py::_swap_real_mcp_for_e2e`` (the repo's
    established per-test swap pattern — the root conftest installs its mocks
    at collection time, so a collection-time swap is impossible). Only this
    test sees the real SDK; the mock is restored in ``finally``.
    """
    saved_top = sys.modules.get("slack_sdk")
    mocked = [k for k in list(sys.modules) if k == "slack_sdk" or k.startswith("slack_sdk.")]
    for name in mocked:
        sys.modules.pop(name, None)
    try:
        importlib.import_module("slack_sdk")  # force-load the real package
    except Exception as exc:  # pragma: no cover - defensive
        if saved_top is not None:
            sys.modules["slack_sdk"] = saved_top
        pytest.skip(f"Real slack_sdk package is not importable: {exc}")
    try:
        yield
    finally:
        real = [k for k in list(sys.modules) if k == "slack_sdk" or k.startswith("slack_sdk.")]
        for name in real:
            sys.modules.pop(name, None)
        if saved_top is not None:
            sys.modules["slack_sdk"] = saved_top


@pytest.fixture
def slack_with_running_status(mock_slack_adapter):
    """Adapter in RUNNING state with a working app for chart-image upload."""
    mock_slack_adapter._status = SourceStatus.RUNNING
    mock_slack_adapter._app = MagicMock()
    return mock_slack_adapter


class TestSlackSingleFilesUploadV2:
    """Task #22 / amendment #9: SINGLE files_upload_v2 per image; NO chat.postMessage(file=)."""

    @pytest.mark.asyncio
    async def test_files_upload_v2_called_per_image(self, slack_with_running_status):
        # Capture calls to _safe_api_call (which is the wrapper for files_upload_v2).
        calls: list[dict] = []

        async def capture(method, **kwargs):
            calls.append({"method": method, "kwargs": kwargs})
            return True, {}

        slack_with_running_status._safe_api_call = capture

        msg = OutgoingMessage(
            external_user_id="T123456:C123456",
            content="Here are 3 charts",
            source_id="slack-main",
            images=[
                _make_slack_att(image_id="1" * 32),
                _make_slack_att(image_id="2" * 32),
                _make_slack_att(image_id="3" * 32),
            ],
        )
        await slack_with_running_status.send(msg)

        # 3 images → 3 files_upload_v2 calls (NO chat.postMessage(file=) for the file itself).
        upload_calls = [c for c in calls if c["method"] == "files_upload_v2"]
        assert len(upload_calls) == 3
        # Each call has the verified kwargs per slack-sdk 3.42.0.
        for upload_call in upload_calls:
            assert "channel" in upload_call["kwargs"]
            assert "filename" in upload_call["kwargs"]
            assert "content" in upload_call["kwargs"]
            assert "initial_comment" in upload_call["kwargs"]
            assert isinstance(upload_call["kwargs"]["content"], bytes)

    @pytest.mark.asyncio
    async def test_initial_comment_truncated(self, slack_with_running_status):
        """content > INITIAL_COMMENT_MAX → initial_comment truncated."""
        calls: list[dict] = []

        async def capture(method, **kwargs):
            calls.append({"method": method, "kwargs": kwargs})
            # files_upload_v2 returns ok + non-empty result; chat.postMessage
            # returns ok with empty result (the existing tests pattern).
            if method == "files_upload_v2":
                return True, {"ok": True, "file": {"id": "F123"}}
            return True, {}

        slack_with_running_status._safe_api_call = capture

        # 5000 chars > INITIAL_COMMENT_MAX (4000)
        long_content = "x" * 5000
        msg = OutgoingMessage(
            external_user_id="T123456:C123456",
            content=long_content,
            source_id="slack-main",
            images=[_make_slack_att()],
        )
        await slack_with_running_status.send(msg)

        upload_calls = [c for c in calls if c["method"] == "files_upload_v2"]
        assert len(upload_calls) == 1
        # initial_comment is truncated to 4000 chars.
        assert len(upload_calls[0]["kwargs"]["initial_comment"]) == 4000

        # Caption > INITIAL_COMMENT_MAX → follow-up chat.postMessage.
        post_calls = [c for c in calls if c["method"] == "chat.postMessage"]
        # The follow-up carries the truncated remainder (1000 chars) —
        # find the call whose `text` is exactly 1000 chars (the regular text
        # call truncates to TEXT_FALLBACK_MAX_LENGTH=500 per existing code).
        follow_ups = [
            c for c in post_calls
            if len(c["kwargs"].get("text", "")) == 1000
        ]
        assert len(follow_ups) == 1, (
            f"Expected 1 follow-up chat.postMessage with text=1000 chars, "
            f"got {len(follow_ups)} (post_calls text lengths: {[len(c['kwargs'].get('text', '')) for c in post_calls]})"
        )

    @pytest.mark.asyncio
    async def test_files_upload_v2_real_sdk_method_transport_stubbed(
        self, slack_with_running_status, real_slack_sdk
    ):
        """Real-SDK-boundary guard (tester VERIFICATION 2026-10-04 finding #1).

        ``AsyncWebClient.files_upload_v2`` is the REAL slack_sdk 3.42.0 method;
        the stub sits at the HTTP-transport layer (fake aiohttp session), NOT
        on the SDK method. A kwarg-name drift (``channel_id=`` vs ``channel=``)
        raises TypeError inside the real signature at the
        ``files_completeUploadExternal`` call frame BEFORE any HTTP request —
        exactly the production break this pins against.
        """
        # The conftest installs a mock ``slack_sdk`` in sys.modules at
        # collection time; the ``real_slack_sdk`` fixture swapped it out for
        # the real package for this test (and restores the mock on teardown).
        from slack_sdk.web.async_client import AsyncWebClient
        session = _FakeSlackHttpSession()
        real_client = AsyncWebClient(token="xoxb-test-fake", session=session)
        slack_with_running_status._app = SimpleNamespace(client=real_client)

        msg = OutgoingMessage(
            external_user_id="T123456:C123456",
            content="chart caption",
            source_id="slack-main",
            images=[_make_slack_att(image_id="c" * 32)],
        )
        ok = await slack_with_running_status.send(msg)

        assert ok is True
        # adapter delivered-id path: the image was confirmed uploaded
        assert msg.delivered_image_ids == ["c" * 32]
        # transport saw the full resolved v2 chain
        urls = [r["url"] for r in session.requests]
        assert any(u.endswith("files.getUploadURLExternal") for u in urls)
        upload_posts = [r for r in session.requests if "files.slack.com/upload/" in r["url"]]
        assert len(upload_posts) == 1
        assert isinstance(upload_posts[0]["req_args"]["data"], bytes)
        completion = next(r for r in session.requests if r["url"].endswith("files.completeUploadExternal"))
        # channel propagated through the REAL signature (channel= -> channel_id= inside the SDK)
        assert completion["req_args"]["params"]["channel_id"] == "C123456"


class TestSlackCapabilityClassifiedBeforeRecord:
    """Task #20 / amendment #4: missing_scope → SlackCapabilityError, NO record_failure.

    F2 (council-review): rerouted from bypassing `_safe_api_call` to stubbing
    `_do_api_call` so the real `acquire_and_execute` chain runs end-to-end.
    The SlackTieredRateLimiter is NOT short-circuited; F1's `SlackCapabilityError:
    raise` guard is exercised. Without F1's fix this test would FAIL (the
    capability error would be swallowed by rate_limiter and converted to
    (False, None), `_call_slack_api` would raise SlackAPIError("Rate limit
    timeout"), and the breaker would record_failure — violating the
    "no record_failure on capability" invariant).
    """

    @pytest.mark.asyncio
    async def test_missing_scope_raises_capability_error(self, slack_with_running_status):
        slack_with_running_status._app = MagicMock()
        slack_with_running_status._slack_capability_flags = set()

        # Stub the lowest layer: _do_api_call. Real chain above
        # (rate_limiter.acquire_and_execute → _call_slack_api → _safe_api_call
        # → _do_api_call) runs UNMOCKED — F1's `except SlackCapabilityError:
        # raise` guard is what makes the exception reach the send() handler.
        async def raises_capability(method, **kwargs):
            if method == "files_upload_v2":
                raise SlackCapabilityError(f"Slack capability error: missing_scope")
            # chat.postMessage (text floor) still succeeds.
            return {"ok": True}

        slack_with_running_status._do_api_call = raises_capability

        initial = slack_with_running_status._circuit_breaker.failure_count

        msg = OutgoingMessage(
            external_user_id="T123456:C123456",
            content="chart",
            source_id="slack-main",
            images=[_make_slack_att()],
        )
        await slack_with_running_status.send(msg)

        # CRITICAL: NO record_failure fired — missing_scope is NOT a transport error.
        assert slack_with_running_status._circuit_breaker.failure_count == initial
        # Capability flag set so subsequent sends short-circuit.
        assert "files_upload_v2" in slack_with_running_status._slack_capability_flags


class TestSlackCapabilityFlagZeroApiCalls:
    """Task #21 / amendment #4: 5 sends → only 1 actual API call (the first).

    After the first missing_scope sets the flag, subsequent sends short-circuit
    BEFORE the API call.

    F2 (council-review): rerouted from bypassing `_safe_api_call` to stubbing
    `_do_api_call` so the real `acquire_and_execute` chain runs end-to-end.
    F1's guard is exercised: SlackCapabilityError must reach the send()
    handler to set the flag (otherwise the catch-all in rate_limiter
    converts it to (False, None) and the flag is never set).
    """

    @pytest.mark.asyncio
    async def test_capability_flag_short_circuits_subsequent_sends(
        self, slack_with_running_status
    ):
        slack_with_running_status._app = MagicMock()
        slack_with_running_status._slack_capability_flags = set()
        slack_with_running_status._slack_capability_warned = False

        api_calls: list[dict] = []

        async def fake_do_api_call(method, **kwargs):
            api_calls.append({"method": method, "kwargs": kwargs})
            if method == "files_upload_v2":
                raise SlackCapabilityError(f"Slack capability error: missing_scope")
            # chat.postMessage succeeds.
            return {"ok": True}

        slack_with_running_status._do_api_call = fake_do_api_call

        msg = OutgoingMessage(
            external_user_id="T123456:C123456",
            content="chart",
            source_id="slack-main",
            images=[_make_slack_att()],
        )

        # 5 sends to the SAME channel.
        for _ in range(5):
            await slack_with_running_status.send(msg)

        # Count files_upload_v2 vs chat.postMessage calls.
        upload_calls = [c for c in api_calls if c["method"] == "files_upload_v2"]
        post_calls = [c for c in api_calls if c["method"] == "chat.postMessage"]
        # First send: 1 files_upload_v2 (raises SlackCapabilityError, sets flag).
        # NOTE: with F9 fix, chat.postMessage fires only when uploaded_count==0
        # or caption_remaining. Here uploaded_count == 0 (capability error
        # is raised BEFORE the ok-and-result branch, so the image is NOT
        # counted as uploaded). So chat.postMessage fires each time to
        # deliver the text (text floor invariant).
        # Sends 2-5: pre-check sees flag → images=None → no upload attempts.
        # Text chat.postMessage still fires each time (text floor invariant).
        assert len(upload_calls) == 1, (
            f"Expected exactly 1 files_upload_v2 call (the first), got {len(upload_calls)}. "
            f"Capability flag must short-circuit subsequent uploads."
        )
        # chat.postMessage fires once per send (text delivery — uploaded_count
        # is 0 because the capability error happens BEFORE we count it).
        assert len(post_calls) == 5
        # consecutive_failures == 0 — never recorded (capability, not transport).
        assert slack_with_running_status._circuit_breaker.failure_count == 0
        # Capability flag set.
        assert "files_upload_v2" in slack_with_running_status._slack_capability_flags


class TestSlackWarnOncePerChannel:
    """Task #24: first missing_scope → ONE WARN log; subsequent do NOT re-WARN.

    F2 + F4 (council-review): rerouted from bypassing `_safe_api_call` to
    stubbing `_do_api_call` (real chain runs), AND changed to GLOBAL scope
    (F4) — first capability error on ANY channel warns once, subsequent
    errors (any channel) stay silent. Includes a fresh-channel test that
    asserts the global scope via a SECOND channel after the first warned.
    """

    @pytest.mark.asyncio
    async def test_warn_once_global_across_channels(
        self, slack_with_running_status, caplog
    ):
        slack_with_running_status._app = MagicMock()
        slack_with_running_status._slack_capability_flags = set()
        slack_with_running_status._slack_capability_warned = False

        async def fake_do_api_call(method, **kwargs):
            if method == "files_upload_v2":
                raise SlackCapabilityError(f"Slack capability error: missing_scope")
            return {"ok": True}

        slack_with_running_status._do_api_call = fake_do_api_call

        import logging as _logging
        caplog.set_level(_logging.WARNING, logger="daemon.sources.adapters.slack.adapter")

        # First send → channel A → WARN.
        msg_a = OutgoingMessage(
            external_user_id="T123456:C000111",
            content="chart",
            source_id="slack-main",
            images=[_make_slack_att(image_id="a" * 32)],
        )
        await slack_with_running_status.send(msg_a)
        first_warn_count = sum(
            1 for r in caplog.records
            if "files:write scope" in r.message or "missing files:write" in r.message
        )
        assert first_warn_count == 1

        # F4: fresh channel B → NO new WARN (global, not per-channel).
        caplog.clear()
        msg_b = OutgoingMessage(
            external_user_id="T123456:C000222",
            content="chart",
            source_id="slack-main",
            images=[_make_slack_att(image_id="b" * 32)],
        )
        await slack_with_running_status.send(msg_b)
        fresh_channel_warn_count = sum(
            1 for r in caplog.records
            if "files:write scope" in r.message or "missing files:write" in r.message
        )
        assert fresh_channel_warn_count == 0, (
            f"F4: fresh channel B should NOT re-WARN (global dedup), "
            f"got {fresh_channel_warn_count} WARN lines"
        )

        # Third send to channel A → still no new WARN (already warned).
        caplog.clear()
        await slack_with_running_status.send(msg_a)
        subsequent_warn_count = sum(
            1 for r in caplog.records
            if "files:write scope" in r.message or "missing files:write" in r.message
        )
        assert subsequent_warn_count == 0


class TestSlackMultiImageNoSilentDrop:
    """Task #47 / iter-003 blocking #2: 3 markers → 3 uploads attempted (NO silent drops).

    F6 (council-review): was vacuous — zero assertions beyond send() not
    raising. Now asserts per-image files_upload_v2 calls, order preserved,
    exactly one `files_upload_v2` per image, NO silent-drop WARNs on the
    happy path, text still delivered (text floor), no chat.postMessage
    double-fire (F9 — initial_comment carries text).
    """

    @pytest.mark.asyncio
    async def test_three_images_three_uploads_no_silent_drop(
        self, slack_with_running_status, caplog
    ):
        slack_with_running_status._app = MagicMock()
        slack_with_running_status._slack_capability_flags = set()
        slack_with_running_status._slack_capability_warned = False

        calls: list[dict] = []

        async def fake_safe_api_call(method, **kwargs):
            calls.append({"method": method, "kwargs": kwargs})
            # All succeed — return a TRUTHY response dict so the adapter's
            # ``if ok and result`` branch counts the image (empty dict {}
            # is falsy and would skip the count + emit the non-ok WARN).
            return True, {"ok": True, "file": {"id": "F123"}}

        slack_with_running_status._safe_api_call = fake_safe_api_call

        import logging as _logging
        caplog.set_level(_logging.WARNING, logger="daemon.sources.adapters.slack.adapter")

        msg = OutgoingMessage(
            external_user_id="T123456:C123456",
            content="charts",
            source_id="slack-main",
            images=[
                _make_slack_att(image_id="1" * 32),
                _make_slack_att(image_id="2" * 32),
                _make_slack_att(image_id="3" * 32),
            ],
        )
        # F9: image-bearing send with text fitting in initial_comment
        # → chat.postMessage must NOT fire (no double-text).
        await slack_with_running_status.send(msg)

        # F6: exactly 3 files_upload_v2 calls, one per image, in order.
        upload_calls = [c for c in calls if c["method"] == "files_upload_v2"]
        assert len(upload_calls) == 3, (
            f"Expected 3 files_upload_v2 calls (one per image, no silent drop), "
            f"got {len(upload_calls)}"
        )
        # Order preserved — image_ids in call order.
        assert "1" * 32 in upload_calls[0]["kwargs"]["filename"] or \
               "11111111" in upload_calls[0]["kwargs"]["filename"]
        assert "22222222" in upload_calls[1]["kwargs"]["filename"]
        assert "33333333" in upload_calls[2]["kwargs"]["filename"]

        # F9: image-bearing send with text fitting in initial_comment
        # → NO chat.postMessage (text travels with the file via
        # initial_comment; double-fire would render the same text twice).
        post_calls = [c for c in calls if c["method"] == "chat.postMessage"]
        assert len(post_calls) == 0, (
            f"F9: expected 0 chat.postMessage calls (initial_comment carries text), "
            f"got {len(post_calls)}: {[c['kwargs'].get('text', '')[:40] for c in post_calls]}"
        )

        # No silent-drop WARNs on the happy path.
        silent_drop_warns = [
            r for r in caplog.records
            if r.levelname == "WARNING" and (
                "decode failed" in r.message
                or "too large" in r.message
                or "skipped" in r.message.lower()
            )
        ]
        assert len(silent_drop_warns) == 0, (
            f"Expected 0 silent-drop WARNs on the happy path, "
            f"got {len(silent_drop_warns)}: {[r.message for r in silent_drop_warns]}"
        )

        # F5: all 3 image_ids reported as delivered (no drops).
        assert sorted(msg.delivered_image_ids) == sorted(["1" * 32, "2" * 32, "3" * 32]), (
            f"F5: expected all 3 image_ids in delivered_image_ids, got {msg.delivered_image_ids}"
        )


class TestSlackOversizeImage:
    """M1: oversize image (>SLACK_FILE_MAX_BYTES) → WARN + skip that image;
    text floor preserved (chat.postMessage still fires)."""

    @pytest.mark.asyncio
    async def test_oversize_image_skipped_text_still_delivers(
        self, slack_with_running_status, caplog, monkeypatch
    ):
        # Shrink the size guard so a 100-byte test image is "oversize" — avoids
        # building a real 1 GB buffer.
        monkeypatch.setattr(
            "daemon.sources.adapters.slack.adapter.SLACK_FILE_MAX_BYTES",
            10,
        )
        slack_with_running_status._slack_capability_flags = set()

        calls: list[dict] = []

        async def capture(method, **kwargs):
            calls.append({"method": method, "kwargs": kwargs})
            if method == "files_upload_v2":
                return True, {"ok": True, "file": {"id": "F123"}}
            return True, {}

        slack_with_running_status._safe_api_call = capture

        msg = OutgoingMessage(
            external_user_id="T123456:C123456",
            content="Here is a chart",
            source_id="slack-main",
            images=[_make_slack_att(image_id="1" * 32, size=100)],
        )

        import logging as _logging
        caplog.set_level(_logging.WARNING, logger="daemon.sources.adapters.slack.adapter")

        # Send still succeeds (text floor invariant).
        assert await slack_with_running_status.send(msg) is True

        # files_upload_v2 was NOT called — the oversize image was skipped.
        upload_calls = [c for c in calls if c["method"] == "files_upload_v2"]
        assert len(upload_calls) == 0, (
            f"Expected 0 files_upload_v2 calls (image oversize → skipped), "
            f"got {len(upload_calls)}"
        )

        # chat.postMessage WAS called — text floor invariant preserved
        # (amendment #8): if all images skip/fail, text still delivers.
        post_calls = [c for c in calls if c["method"] == "chat.postMessage"]
        assert len(post_calls) >= 1, (
            f"Expected at least 1 chat.postMessage call (text floor), "
            f"got {len(post_calls)}"
        )

        # Exactly one WARN was logged for the oversize image — no silent drop.
        oversize_warns = [
            r for r in caplog.records
            if r.levelname == "WARNING" and "too large" in r.message
        ]
        assert len(oversize_warns) == 1, (
            f"Expected 1 WARN for oversize image, got {len(oversize_warns)}: "
            f"{[r.message for r in oversize_warns]}"
        )
        # The WARN carries image_id[:8] + size, NEVER bytes.
        assert "11111111" in oversize_warns[0].message
        assert "size=" in oversize_warns[0].message
