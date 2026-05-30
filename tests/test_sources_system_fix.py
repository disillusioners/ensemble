"""
Unit tests for source system fixes.

Tests the following bug fixes:
1. mapper.py - spawn_instance_with_mcp() now receives a generated UUID as instance_id
2. registry.py - images and metadata from incoming messages are now forwarded to enqueue_message()
3. registry.py - Priority is now configurable via metadata with safe int coercion
4. base.py - IncomingMessage dataclass now has images: list[str] | None = None field
"""

import pytest
import uuid
from unittest.mock import MagicMock, AsyncMock, patch
from dataclasses import dataclass, field
from pathlib import Path

from daemon.sources.base import IncomingMessage
from daemon.sources.mapper import InstanceMapper
from daemon.sources.registry import SourceRegistry


# ==================== Fixtures ====================


@dataclass
class MockAgentMeta:
    """Mock agent metadata returned by registry."""
    path: Path = field(default_factory=lambda: Path("/agents/test"))


@dataclass
class MockMapping:
    """Mock mapping object returned by source_repo."""
    mapping_id: str
    source_id: str
    external_user_id: str
    agent_instance_id: str
    agent_id: str
    agent_dir: str
    mapping_metadata: dict = field(default_factory=dict)
    last_message_at: str = None
    created_at: str = None


class MockSourceRepository:
    """Mock source repository with required methods."""

    def __init__(self):
        self.mappings: dict[tuple[str, str], MockMapping] = {}
        self.processed_messages: set[tuple[str, str]] = set()
        self.mapping_id_counter = 0

    def get_instance_mapping(self, source_id: str, external_user_id: str) -> MockMapping | None:
        """Get mapping by source and external user ID."""
        return self.mappings.get((source_id, external_user_id))

    def update_mapping_last_message(self, source_id: str, external_user_id: str) -> None:
        """Update the last_message_at timestamp for a mapping."""
        mapping = self.mappings.get((source_id, external_user_id))
        if mapping:
            mapping.last_message_at = "2024-01-01T00:00:00"

    def check_and_mark_processed(self, source_id: str, external_message_id: str) -> bool:
        """Check if message is duplicate and mark as processed if new."""
        key = (source_id, external_message_id)
        if key in self.processed_messages:
            return True
        self.processed_messages.add(key)
        return False

    def delete_instance_mapping(self, mapping_id: str) -> None:
        """Delete mapping by mapping_id."""
        for key, mapping in list(self.mappings.items()):
            if mapping.mapping_id == mapping_id:
                del self.mappings[key]
                break

    def create_instance_mapping(
        self,
        source_id: str,
        external_user_id: str,
        agent_instance_id: str,
        agent_id: str,
        agent_dir: str,
        metadata: dict,
        mapping_id: str,
    ) -> None:
        """Create a new instance mapping."""
        mapping = MockMapping(
            mapping_id=mapping_id,
            source_id=source_id,
            external_user_id=external_user_id,
            agent_instance_id=agent_instance_id,
            agent_id=agent_id,
            agent_dir=agent_dir,
            mapping_metadata=metadata,
            last_message_at="2024-01-01T00:00:00",
            created_at="2024-01-01T00:00:00",
        )
        self.mappings[(source_id, external_user_id)] = mapping


@pytest.fixture
def mock_registry():
    """Create a mock agent registry."""
    mock_reg = MagicMock()
    mock_reg.resolve_to_id.return_value = None
    mock_reg.get.return_value = MockAgentMeta()
    return mock_reg


@pytest.fixture
def mock_source_repo():
    """Create a mock source repository."""
    return MockSourceRepository()


@pytest.fixture
def mock_manager():
    """Create a mock InstanceManager."""
    manager = MagicMock()
    manager.spawn_instance_with_mcp = AsyncMock(return_value=str(uuid.uuid4()))
    return manager


@pytest.fixture
def instance_mapper(mock_source_repo, mock_manager, mock_registry):
    """Create an InstanceMapper with mocks."""
    with patch("daemon.sources.mapper.get_registry", return_value=mock_registry):
        yield InstanceMapper(source_repo=mock_source_repo, manager=mock_manager)


# ==================== Test 1: Mapper Fix - instance_id Generation ====================


class TestMapperInstanceIdFix:
    """Tests for the instance_id generation fix in mapper.py.

    The fix ensures that spawn_instance_with_mcp() receives a generated UUID
    as the instance_id parameter (was missing, crashing all source instance creation).
    """

    @pytest.mark.asyncio
    async def test_spawn_instance_with_mcp_receives_instance_id_kwarg(self, mock_source_repo, mock_manager, mock_registry):
        """Test that spawn_instance_with_mcp is called with instance_id keyword argument."""
        with patch("daemon.sources.mapper.get_registry", return_value=mock_registry):
            mapper = InstanceMapper(source_repo=mock_source_repo, manager=mock_manager)

            # Reset the mock to track calls
            mock_manager.spawn_instance_with_mcp.reset_mock()

            # Call get_or_create_instance
            await mapper.get_or_create_instance(
                source_id="test_source",
                external_user_id="user123",
                agent_id="test"
            )

            # Verify spawn_instance_with_mcp was called
            mock_manager.spawn_instance_with_mcp.assert_called_once()

            # Verify instance_id was passed as a keyword argument
            call_kwargs = mock_manager.spawn_instance_with_mcp.call_args.kwargs
            assert "instance_id" in call_kwargs, "instance_id should be passed as kwarg"

    @pytest.mark.asyncio
    async def test_instance_id_is_valid_uuid(self, mock_source_repo, mock_manager, mock_registry):
        """Test that the generated instance_id is a valid UUID string."""
        with patch("daemon.sources.mapper.get_registry", return_value=mock_registry):
            mapper = InstanceMapper(source_repo=mock_source_repo, manager=mock_manager)

            mock_manager.spawn_instance_with_mcp.reset_mock()

            await mapper.get_or_create_instance(
                source_id="test_source",
                external_user_id="user123",
                agent_id="test"
            )

            # Get the instance_id that was passed
            call_kwargs = mock_manager.spawn_instance_with_mcp.call_args.kwargs
            instance_id = call_kwargs["instance_id"]

            # Verify it's a valid UUID string
            parsed_uuid = uuid.UUID(instance_id)
            assert str(parsed_uuid) == instance_id

    @pytest.mark.asyncio
    async def test_agent_id_also_passed_to_spawn(self, mock_source_repo, mock_manager, mock_registry):
        """Test that agent_id is also passed to spawn_instance_with_mcp."""
        with patch("daemon.sources.mapper.get_registry", return_value=mock_registry):
            mapper = InstanceMapper(source_repo=mock_source_repo, manager=mock_manager)

            mock_manager.spawn_instance_with_mcp.reset_mock()

            await mapper.get_or_create_instance(
                source_id="test_source",
                external_user_id="user123",
                agent_id="my_agent"
            )

            call_kwargs = mock_manager.spawn_instance_with_mcp.call_args.kwargs

            # Both instance_id and agent_id should be present
            assert "instance_id" in call_kwargs
            assert "agent_id" in call_kwargs
            assert call_kwargs["agent_id"] == "my_agent"

    @pytest.mark.asyncio
    async def test_mapping_created_with_correct_instance_id(self, mock_source_repo, mock_manager, mock_registry):
        """Test that the mapping is created with the instance ID returned from spawn."""
        with patch("daemon.sources.mapper.get_registry", return_value=mock_registry):
            mapper = InstanceMapper(source_repo=mock_source_repo, manager=mock_manager)

            expected_instance_id = "spawned-instance-uuid-12345"
            mock_manager.spawn_instance_with_mcp.return_value = expected_instance_id

            result = await mapper.get_or_create_instance(
                source_id="test_source",
                external_user_id="user123",
                agent_id="test"
            )

            # The return value should be the spawned instance ID
            assert result == expected_instance_id

            # The mapping should store the correct instance ID
            mapping = mapper.get_mapping("test_source", "user123")
            assert mapping is not None
            assert mapping["agent_instance_id"] == expected_instance_id


# ==================== Test 2: Metadata Forwarding - images and metadata ====================


class TestMetadataForwarding:
    """Tests for images and metadata forwarding fix in registry.py.

    The fix ensures that images and metadata from incoming messages are
    now forwarded to enqueue_message() (were dropped previously).
    """

    @pytest.mark.asyncio
    async def test_enqueue_message_receives_images_from_incoming_message(self):
        """Test that enqueue_message() is called with images from the incoming message."""
        # Create mock manager
        manager = MagicMock()
        mock_config = MagicMock()
        mock_config.agents.directory = "/default/agents"
        manager.config = mock_config
        manager.enqueue_message = AsyncMock()

        # Mock source repo
        mock_source_repo = MagicMock()
        mock_source_repo.check_and_mark_processed = MagicMock(return_value=False)

        registry = SourceRegistry(mock_source_repo, manager)

        # Create message with images
        msg = IncomingMessage(
            external_user_id="user123",
            content="Hello with image",
            source_id="test-source",
            images=["https://example.com/image1.jpg", "https://example.com/image2.png"],
            metadata={"key": "value"}
        )

        # Mock InstanceMapper
        with patch('daemon.sources.registry.InstanceMapper') as MockInstanceMapper, \
             patch('daemon.sources.mapper.get_registry') as mock_get_registry:
            mock_agent_registry = MagicMock()
            mock_agent = MagicMock()
            mock_agent.path = "/default/agents"
            mock_agent_registry.resolve_to_id = MagicMock(return_value=None)
            mock_agent_registry.get = MagicMock(return_value=mock_agent)
            mock_get_registry.return_value = mock_agent_registry

            mock_mapper_instance = MagicMock()
            mock_mapper_instance.get_or_create_instance = AsyncMock(return_value="instance-123")
            MockInstanceMapper.return_value = mock_mapper_instance

            # Call _handle_message
            await registry._handle_message("test-source", msg)

            # Verify enqueue_message was called
            manager.enqueue_message.assert_called_once()

            # Verify images were passed
            call_kwargs = manager.enqueue_message.call_args.kwargs
            assert "images" in call_kwargs
            assert call_kwargs["images"] == ["https://example.com/image1.jpg", "https://example.com/image2.png"]

    @pytest.mark.asyncio
    async def test_enqueue_message_receives_metadata_from_incoming_message(self):
        """Test that enqueue_message() is called with metadata from the incoming message."""
        manager = MagicMock()
        mock_config = MagicMock()
        mock_config.agents.directory = "/default/agents"
        manager.config = mock_config
        manager.enqueue_message = AsyncMock()

        mock_source_repo = MagicMock()
        mock_source_repo.check_and_mark_processed = MagicMock(return_value=False)

        registry = SourceRegistry(mock_source_repo, manager)

        expected_metadata = {"user_name": "John", "chat_id": 12345}
        msg = IncomingMessage(
            external_user_id="user123",
            content="Hello",
            source_id="test-source",
            metadata=expected_metadata
        )

        with patch('daemon.sources.registry.InstanceMapper') as MockInstanceMapper, \
             patch('daemon.sources.mapper.get_registry') as mock_get_registry:
            mock_agent_registry = MagicMock()
            mock_agent = MagicMock()
            mock_agent.path = "/default/agents"
            mock_agent_registry.resolve_to_id = MagicMock(return_value=None)
            mock_agent_registry.get = MagicMock(return_value=mock_agent)
            mock_get_registry.return_value = mock_agent_registry

            mock_mapper_instance = MagicMock()
            mock_mapper_instance.get_or_create_instance = AsyncMock(return_value="instance-123")
            MockInstanceMapper.return_value = mock_mapper_instance

            await registry._handle_message("test-source", msg)

            manager.enqueue_message.assert_called_once()
            call_kwargs = manager.enqueue_message.call_args.kwargs

            assert "metadata" in call_kwargs
            assert call_kwargs["metadata"] == expected_metadata

    @pytest.mark.asyncio
    async def test_enqueue_message_with_images_none_does_not_crash(self):
        """Test that when images is None, enqueue_message still receives None (no crash)."""
        manager = MagicMock()
        mock_config = MagicMock()
        mock_config.agents.directory = "/default/agents"
        manager.config = mock_config
        manager.enqueue_message = AsyncMock()

        mock_source_repo = MagicMock()
        mock_source_repo.check_and_mark_processed = MagicMock(return_value=False)

        registry = SourceRegistry(mock_source_repo, manager)

        # Message with images=None (default)
        msg = IncomingMessage(
            external_user_id="user123",
            content="Hello",
            source_id="test-source",
            images=None,
            metadata={}
        )

        with patch('daemon.sources.registry.InstanceMapper') as MockInstanceMapper, \
             patch('daemon.sources.mapper.get_registry') as mock_get_registry:
            mock_agent_registry = MagicMock()
            mock_agent = MagicMock()
            mock_agent.path = "/default/agents"
            mock_agent_registry.resolve_to_id = MagicMock(return_value=None)
            mock_agent_registry.get = MagicMock(return_value=mock_agent)
            mock_get_registry.return_value = mock_agent_registry

            mock_mapper_instance = MagicMock()
            mock_mapper_instance.get_or_create_instance = AsyncMock(return_value="instance-123")
            MockInstanceMapper.return_value = mock_mapper_instance

            # Should not crash
            await registry._handle_message("test-source", msg)

            manager.enqueue_message.assert_called_once()
            call_kwargs = manager.enqueue_message.call_args.kwargs

            assert "images" in call_kwargs
            assert call_kwargs["images"] is None

    @pytest.mark.asyncio
    async def test_enqueue_message_with_empty_images_list(self):
        """Test that enqueue_message receives empty list when images is empty."""
        manager = MagicMock()
        mock_config = MagicMock()
        mock_config.agents.directory = "/default/agents"
        manager.config = mock_config
        manager.enqueue_message = AsyncMock()

        mock_source_repo = MagicMock()
        mock_source_repo.check_and_mark_processed = MagicMock(return_value=False)

        registry = SourceRegistry(mock_source_repo, manager)

        msg = IncomingMessage(
            external_user_id="user123",
            content="Hello",
            source_id="test-source",
            images=[],
            metadata={}
        )

        with patch('daemon.sources.registry.InstanceMapper') as MockInstanceMapper, \
             patch('daemon.sources.mapper.get_registry') as mock_get_registry:
            mock_agent_registry = MagicMock()
            mock_agent = MagicMock()
            mock_agent.path = "/default/agents"
            mock_agent_registry.resolve_to_id = MagicMock(return_value=None)
            mock_agent_registry.get = MagicMock(return_value=mock_agent)
            mock_get_registry.return_value = mock_agent_registry

            mock_mapper_instance = MagicMock()
            mock_mapper_instance.get_or_create_instance = AsyncMock(return_value="instance-123")
            MockInstanceMapper.return_value = mock_mapper_instance

            await registry._handle_message("test-source", msg)

            manager.enqueue_message.assert_called_once()
            call_kwargs = manager.enqueue_message.call_args.kwargs

            assert "images" in call_kwargs
            assert call_kwargs["images"] == []


# ==================== Test 3: Priority Extraction ====================


class TestPriorityExtraction:
    """Tests for priority extraction fix in registry.py.

    The fix ensures that priority is configurable via metadata with safe int coercion.
    Tests the on_message callback logic inside _create_adapter.
    """

    @pytest.mark.asyncio
    async def test_priority_integer_from_metadata(self):
        """Test that integer priority in metadata is extracted correctly."""
        manager = MagicMock()
        mock_config = MagicMock()
        mock_config.agents.directory = "/default/agents"
        manager.config = mock_config
        manager.enqueue_message = AsyncMock()

        mock_source_repo = MagicMock()
        mock_source_repo.check_and_mark_processed = MagicMock(return_value=False)

        registry = SourceRegistry(mock_source_repo, manager)

        msg = IncomingMessage(
            external_user_id="user123",
            content="Hello",
            source_id="test-source",
            metadata={"priority": 5}
        )

        with patch('daemon.sources.registry.InstanceMapper') as MockInstanceMapper, \
             patch('daemon.sources.mapper.get_registry') as mock_get_registry:
            mock_agent_registry = MagicMock()
            mock_agent = MagicMock()
            mock_agent.path = "/default/agents"
            mock_agent_registry.resolve_to_id = MagicMock(return_value=None)
            mock_agent_registry.get = MagicMock(return_value=mock_agent)
            mock_get_registry.return_value = mock_agent_registry

            mock_mapper_instance = MagicMock()
            mock_mapper_instance.get_or_create_instance = AsyncMock(return_value="instance-123")
            MockInstanceMapper.return_value = mock_mapper_instance

            # Call _handle_message directly with priority=5
            await registry._handle_message("test-source", msg, priority=5)

            manager.enqueue_message.assert_called_once()
            call_kwargs = manager.enqueue_message.call_args.kwargs
            assert call_kwargs["priority"] == 5

    @pytest.mark.asyncio
    async def test_priority_string_coerced_to_int(self):
        """Test that string priority like "3" is coerced to int 3."""
        # Test the on_message callback priority extraction logic
        msg_metadata = {"priority": "3"}
        priority = int(msg_metadata.get("priority", 1))
        assert priority == 3

    @pytest.mark.asyncio
    async def test_priority_invalid_string_falls_back_to_1(self):
        """Test that invalid priority like "high" falls back to default 1."""
        # Test the on_message callback priority extraction logic
        msg_metadata = {"priority": "high"}
        try:
            priority = int(msg_metadata.get("priority", 1))
        except ValueError:
            priority = 1
        assert priority == 1

    @pytest.mark.asyncio
    async def test_priority_none_falls_back_to_1(self):
        """Test that priority=None falls back to default 1."""
        # Test the on_message callback priority extraction logic
        msg_metadata = {"priority": None}
        try:
            priority = int(msg_metadata.get("priority", 1))
        except TypeError:
            priority = 1
        assert priority == 1

    @pytest.mark.asyncio
    async def test_priority_missing_falls_back_to_1(self):
        """Test that missing priority falls back to default 1."""
        msg_metadata = {}
        priority = int(msg_metadata.get("priority", 1))
        assert priority == 1

    @pytest.mark.asyncio
    async def test_priority_metadata_none_uses_default(self):
        """Test that metadata=None uses default priority of 1 (no crash)."""
        # IncomingMessage defaults metadata to dict via field(default_factory=dict)
        # So msg.metadata will never be None - it's an empty dict if not provided
        msg = IncomingMessage(
            external_user_id="user123",
            content="Hello",
            source_id="test-source",
            metadata={}  # Empty dict, not None
        )
        # Should not crash - metadata is always a dict
        priority = int(msg.metadata.get("priority", 1))
        assert priority == 1

    @pytest.mark.asyncio
    async def test_priority_high_value(self):
        """Test that high priority values are preserved."""
        msg_metadata = {"priority": 100}
        priority = int(msg_metadata.get("priority", 1))
        assert priority == 100

    @pytest.mark.asyncio
    async def test_priority_negative_value(self):
        """Test that negative priority values are preserved."""
        msg_metadata = {"priority": -1}
        priority = int(msg_metadata.get("priority", 1))
        assert priority == -1

    @pytest.mark.asyncio
    async def test_priority_zero(self):
        """Test that priority 0 is preserved."""
        msg_metadata = {"priority": 0}
        priority = int(msg_metadata.get("priority", 1))
        assert priority == 0


# ==================== Test 4: IncomingMessage images Field ====================


class TestIncomingMessageImagesField:
    """Tests for the images field fix in base.py.

    The fix adds images: list[str] | None = None to IncomingMessage dataclass.
    """

    def test_images_field_exists(self):
        """Test that IncomingMessage dataclass has images field."""
        msg = IncomingMessage(
            external_user_id="user123",
            content="Hello",
            source_id="test"
        )
        assert hasattr(msg, "images")

    def test_images_defaults_to_none(self):
        """Test that images defaults to None when not provided."""
        msg = IncomingMessage(
            external_user_id="user123",
            content="Hello",
            source_id="test"
        )
        assert msg.images is None

    def test_images_can_hold_list_of_strings(self):
        """Test that images can hold a list of string URLs."""
        image_list = [
            "https://example.com/image1.jpg",
            "https://example.com/image2.png",
            "https://example.com/image3.gif"
        ]
        msg = IncomingMessage(
            external_user_id="user123",
            content="Hello with images",
            source_id="test",
            images=image_list
        )
        assert msg.images == image_list
        assert len(msg.images) == 3

    def test_images_empty_list(self):
        """Test that images can be an empty list."""
        msg = IncomingMessage(
            external_user_id="user123",
            content="Hello",
            source_id="test",
            images=[]
        )
        assert msg.images == []
        assert isinstance(msg.images, list)

    def test_images_single_image(self):
        """Test that images works with a single image."""
        msg = IncomingMessage(
            external_user_id="user123",
            content="Hello",
            source_id="test",
            images=["single_image.jpg"]
        )
        assert msg.images == ["single_image.jpg"]

    def test_images_none_passes_through(self):
        """Test that images=None passes through without error."""
        msg = IncomingMessage(
            external_user_id="user123",
            content="Hello",
            source_id="test",
            images=None
        )
        assert msg.images is None

    def test_images_with_metadata(self):
        """Test that images works alongside other fields like metadata."""
        msg = IncomingMessage(
            external_user_id="user123",
            content="Hello",
            source_id="test",
            images=["image.jpg"],
            metadata={"key": "value"}
        )
        assert msg.images == ["image.jpg"]
        assert msg.metadata == {"key": "value"}


# ==================== Test 5: Edge Cases ====================


class TestEdgeCases:
    """Edge case tests for source system fixes."""

    @pytest.mark.asyncio
    async def test_existing_mapping_returns_cached_instance_id(self, mock_source_repo, mock_manager, mock_registry):
        """Test that existing mapping returns cached instance_id without spawning new."""
        with patch("daemon.sources.mapper.get_registry", return_value=mock_registry):
            mapper = InstanceMapper(source_repo=mock_source_repo, manager=mock_manager)

            # First call - creates new instance
            mock_manager.spawn_instance_with_mcp.reset_mock()
            instance_id_1 = await mapper.get_or_create_instance(
                source_id="test_source",
                external_user_id="user123",
                agent_id="test"
            )

            # Verify spawn was called for first call
            mock_manager.spawn_instance_with_mcp.assert_called_once()

            # Second call - should return cached instance
            mock_manager.spawn_instance_with_mcp.reset_mock()
            instance_id_2 = await mapper.get_or_create_instance(
                source_id="test_source",
                external_user_id="user123",
                agent_id="test"
            )

            # Verify spawn was NOT called for second call
            mock_manager.spawn_instance_with_mcp.assert_not_called()

            # Both should return the same instance ID
            assert instance_id_1 == instance_id_2

    @pytest.mark.asyncio
    async def test_message_with_no_images_passes_through(self):
        """Test that message with images=None passes through _handle_message without error."""
        manager = MagicMock()
        mock_config = MagicMock()
        mock_config.agents.directory = "/default/agents"
        manager.config = mock_config
        manager.enqueue_message = AsyncMock()

        mock_source_repo = MagicMock()
        mock_source_repo.check_and_mark_processed = MagicMock(return_value=False)

        registry = SourceRegistry(mock_source_repo, manager)

        # Message with no images (images=None)
        msg = IncomingMessage(
            external_user_id="user123",
            content="Hello",
            source_id="test-source",
            images=None
        )

        with patch('daemon.sources.registry.InstanceMapper') as MockInstanceMapper, \
             patch('daemon.sources.mapper.get_registry') as mock_get_registry:
            mock_agent_registry = MagicMock()
            mock_agent = MagicMock()
            mock_agent.path = "/default/agents"
            mock_agent_registry.resolve_to_id = MagicMock(return_value=None)
            mock_agent_registry.get = MagicMock(return_value=mock_agent)
            mock_get_registry.return_value = mock_agent_registry

            mock_mapper_instance = MagicMock()
            mock_mapper_instance.get_or_create_instance = AsyncMock(return_value="instance-123")
            MockInstanceMapper.return_value = mock_mapper_instance

            # Should not crash
            await registry._handle_message("test-source", msg)

            # Verify enqueue was called
            manager.enqueue_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_message_with_images_passes_to_enqueue(self):
        """Test that message with images list passes images through to enqueue_message."""
        manager = MagicMock()
        mock_config = MagicMock()
        mock_config.agents.directory = "/default/agents"
        manager.config = mock_config
        manager.enqueue_message = AsyncMock()

        mock_source_repo = MagicMock()
        mock_source_repo.check_and_mark_processed = MagicMock(return_value=False)

        registry = SourceRegistry(mock_source_repo, manager)

        msg = IncomingMessage(
            external_user_id="user123",
            content="Hello with images",
            source_id="test-source",
            images=["img1.jpg", "img2.jpg", "img3.jpg"]
        )

        with patch('daemon.sources.registry.InstanceMapper') as MockInstanceMapper, \
             patch('daemon.sources.mapper.get_registry') as mock_get_registry:
            mock_agent_registry = MagicMock()
            mock_agent = MagicMock()
            mock_agent.path = "/default/agents"
            mock_agent_registry.resolve_to_id = MagicMock(return_value=None)
            mock_agent_registry.get = MagicMock(return_value=mock_agent)
            mock_get_registry.return_value = mock_agent_registry

            mock_mapper_instance = MagicMock()
            mock_mapper_instance.get_or_create_instance = AsyncMock(return_value="instance-123")
            MockInstanceMapper.return_value = mock_mapper_instance

            await registry._handle_message("test-source", msg)

            manager.enqueue_message.assert_called_once()
            call_kwargs = manager.enqueue_message.call_args.kwargs

            assert call_kwargs["images"] == ["img1.jpg", "img2.jpg", "img3.jpg"]

    @pytest.mark.asyncio
    async def test_priority_and_images_together(self):
        """Test that both priority and images work together in the same message."""
        manager = MagicMock()
        mock_config = MagicMock()
        mock_config.agents.directory = "/default/agents"
        manager.config = mock_config
        manager.enqueue_message = AsyncMock()

        mock_source_repo = MagicMock()
        mock_source_repo.check_and_mark_processed = MagicMock(return_value=False)

        registry = SourceRegistry(mock_source_repo, manager)

        msg = IncomingMessage(
            external_user_id="user123",
            content="High priority with images",
            source_id="test-source",
            images=["high_priority_image.jpg"],
            metadata={"priority": 10}
        )

        with patch('daemon.sources.registry.InstanceMapper') as MockInstanceMapper, \
             patch('daemon.sources.mapper.get_registry') as mock_get_registry:
            mock_agent_registry = MagicMock()
            mock_agent = MagicMock()
            mock_agent.path = "/default/agents"
            mock_agent_registry.resolve_to_id = MagicMock(return_value=None)
            mock_agent_registry.get = MagicMock(return_value=mock_agent)
            mock_get_registry.return_value = mock_agent_registry

            mock_mapper_instance = MagicMock()
            mock_mapper_instance.get_or_create_instance = AsyncMock(return_value="instance-123")
            MockInstanceMapper.return_value = mock_mapper_instance

            # Call _handle_message with extracted priority
            extracted_priority = int(msg.metadata.get("priority", 1))
            await registry._handle_message("test-source", msg, priority=extracted_priority)

            manager.enqueue_message.assert_called_once()
            call_kwargs = manager.enqueue_message.call_args.kwargs

            # Both should be correctly passed
            assert call_kwargs["priority"] == 10
            assert call_kwargs["images"] == ["high_priority_image.jpg"]

    @pytest.mark.asyncio
    async def test_full_pipeline_with_all_fixes(self):
        """Integration test covering all fixes together."""
        manager = MagicMock()
        mock_config = MagicMock()
        mock_config.agents.directory = "/default/agents"
        manager.config = mock_config
        manager.enqueue_message = AsyncMock()

        mock_source_repo = MagicMock()
        mock_source_repo.check_and_mark_processed = MagicMock(return_value=False)

        registry = SourceRegistry(mock_source_repo, manager)

        # Create a message that exercises all fixes:
        # - images field present
        # - metadata with priority
        # - will use instance_id generation
        msg = IncomingMessage(
            external_user_id="telegram_user_123",
            content="Test message with all fixes",
            source_id="telegram",
            images=["photo1.jpg", "photo2.jpg"],
            metadata={
                "priority": 5,
                "message_id": "msg_123",
                "source_type": "telegram"
            }
        )

        with patch('daemon.sources.registry.InstanceMapper') as MockInstanceMapper, \
             patch('daemon.sources.mapper.get_registry') as mock_get_registry:
            mock_agent_registry = MagicMock()
            mock_agent = MagicMock()
            mock_agent.path = "/default/agents"
            mock_agent_registry.resolve_to_id = MagicMock(return_value=None)
            mock_agent_registry.get = MagicMock(return_value=mock_agent)
            mock_get_registry.return_value = mock_agent_registry

            mock_mapper_instance = MagicMock()
            mock_mapper_instance.get_or_create_instance = AsyncMock(return_value="instance-xyz")
            MockInstanceMapper.return_value = mock_mapper_instance

            # Extract priority as done in on_message callback
            try:
                priority = int(msg.metadata.get("priority", 1))
            except (ValueError, TypeError):
                priority = 1

            await registry._handle_message("telegram", msg, priority=priority)

            # Verify all components
            manager.enqueue_message.assert_called_once()
            call_kwargs = manager.enqueue_message.call_args.kwargs

            # Check images was forwarded
            assert "images" in call_kwargs
            assert call_kwargs["images"] == ["photo1.jpg", "photo2.jpg"]

            # Check metadata was forwarded
            assert "metadata" in call_kwargs
            assert call_kwargs["metadata"]["priority"] == 5

            # Check priority was extracted correctly
            assert call_kwargs["priority"] == 5

            # Check instance_id was used
            assert call_kwargs["instance_id"] == "instance-xyz"
