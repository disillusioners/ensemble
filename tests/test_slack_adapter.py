"""Tests for SlackAdapter implementation."""

import pytest
import time as time_module
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from daemon.sources.adapters.slack.adapter import (
    SlackAdapter,
    SlackAPIError,
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
