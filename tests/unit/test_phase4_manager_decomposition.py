"""Tests for Phase 4 InstanceManager decomposition into facade pattern.

This module verifies that the InstanceManager has been correctly decomposed into
a facade pattern with 7 service classes, and that all public methods, module-level
functions, and inner classes remain importable and functional.

Phase 4 decomposition introduced:
- 7 service classes in daemon/services/
- InstanceManager facade delegating to services
- Module-level functions preserved at daemon.manager module level
- Inner classes (callbacks, result types) still importable from daemon.manager
"""

import inspect
from unittest.mock import MagicMock, AsyncMock, patch

import pytest


class TestFacadeDelegation:
    """Test that InstanceManager facade correctly delegates to all 7 services.

    Verifies:
    - All 7 service attributes exist on InstanceManager
    - Each service attribute is an instance of the correct class
    - Services are properly initialized with manager reference
    """

    def test_manager_has_all_seven_service_attributes(self):
        """InstanceManager should have 7 service attributes defined in __init__.

        We verify this by checking the __init__ method's source code for attribute assignments.
        Direct instantiation would fail without proper setup, so we inspect the source.
        """
        from daemon.manager import InstanceManager
        import inspect

        # Get the __init__ source code
        init_source = inspect.getsource(InstanceManager.__init__)

        expected_services = [
            "_cancellation_service",
            "_events_service",
            "_title_gen_service",
            "_child_reports_service",
            "_error_reporting_service",
            "_messaging_service",
            "_lifecycle_service",
        ]

        for svc in expected_services:
            assert svc in init_source, f"Service attribute {svc} not found in __init__"

    def test_cancellation_service_is_correct_type(self):
        """_cancellation_service should be instance of CancellationService."""
        from daemon.manager import InstanceManager
        from daemon.services.cancellation import CancellationService

        manager = InstanceManager.__new__(InstanceManager)
        manager._cancellation_service = CancellationService(manager=manager)

        assert isinstance(
            manager._cancellation_service, CancellationService
        ), "_cancellation_service should be CancellationService instance"

    def test_events_service_is_correct_type(self):
        """_events_service should be instance of EventPublisherService."""
        from daemon.manager import InstanceManager
        from daemon.services.event_publisher import EventPublisherService

        manager = InstanceManager.__new__(InstanceManager)
        manager._events_service = EventPublisherService(manager=manager)

        assert isinstance(
            manager._events_service, EventPublisherService
        ), "_events_service should be EventPublisherService instance"

    def test_title_gen_service_is_correct_type(self):
        """_title_gen_service should be instance of TitleGenerationService."""
        from daemon.manager import InstanceManager
        from daemon.services.title_generation import TitleGenerationService

        manager = InstanceManager.__new__(InstanceManager)
        manager._title_gen_service = TitleGenerationService(manager=manager)

        assert isinstance(
            manager._title_gen_service, TitleGenerationService
        ), "_title_gen_service should be TitleGenerationService instance"

    def test_child_reports_service_is_correct_type(self):
        """_child_reports_service should be instance of ChildReportsService."""
        from daemon.manager import InstanceManager
        from daemon.services.child_reports import ChildReportsService

        manager = InstanceManager.__new__(InstanceManager)
        manager._child_reports_service = ChildReportsService(
            manager=manager, events_service=MagicMock()
        )

        assert isinstance(
            manager._child_reports_service, ChildReportsService
        ), "_child_reports_service should be ChildReportsService instance"

    def test_error_reporting_service_is_correct_type(self):
        """_error_reporting_service should be instance of ErrorReportingService."""
        from daemon.manager import InstanceManager
        from daemon.services.error_reporting import ErrorReportingService

        manager = InstanceManager.__new__(InstanceManager)
        manager._error_reporting_service = ErrorReportingService(
            manager=manager, events_service=MagicMock()
        )

        assert isinstance(
            manager._error_reporting_service, ErrorReportingService
        ), "_error_reporting_service should be ErrorReportingService instance"

    def test_messaging_service_is_correct_type(self):
        """_messaging_service should be instance of InstanceMessagingService."""
        from daemon.manager import InstanceManager
        from daemon.services.instance_messaging import InstanceMessagingService

        manager = InstanceManager.__new__(InstanceManager)
        manager._messaging_service = InstanceMessagingService(
            manager=manager,
            cancellation_service=MagicMock(),
            child_reports_service=MagicMock(),
            events_service=MagicMock(),
        )

        assert isinstance(
            manager._messaging_service, InstanceMessagingService
        ), "_messaging_service should be InstanceMessagingService instance"

    def test_lifecycle_service_is_correct_type(self):
        """_lifecycle_service should be instance of InstanceLifecycleService."""
        from daemon.manager import InstanceManager
        from daemon.services.instance_lifecycle import InstanceLifecycleService

        manager = InstanceManager.__new__(InstanceManager)
        manager._job_queue_service = MagicMock()
        manager._lifecycle_service = InstanceLifecycleService(
            manager=manager,
            cancellation_service=MagicMock(),
            events_service=MagicMock(),
            job_queue_service=manager._job_queue_service,
        )

        assert isinstance(
            manager._lifecycle_service, InstanceLifecycleService
        ), "_lifecycle_service should be InstanceLifecycleService instance"


class TestPublicMethodsExist:
    """Test that key public methods exist and are callable on InstanceManager.

    Verifies the facade methods exist and delegate to the correct services.
    Uses mocking to verify delegation without full implementation.
    """

    def test_spawn_instance_exists_and_callable(self):
        """spawn_instance method should exist and be callable."""
        from daemon.manager import InstanceManager

        assert hasattr(InstanceManager, "spawn_instance")
        assert callable(getattr(InstanceManager, "spawn_instance"))

    @pytest.mark.skip(
        reason="T6b / D7 LOCKED 2026-08-30: Manager.send_message deleted. "
        "Use manager.enqueue_message (already pinned below)."
    )
    def test_send_message_exists_and_callable(self):
        """send_message method should exist and be callable.

        wc-wake-report-integrity (T6b, D7 LOCKED 2026-08-30): the
        legacy ``Manager.send_message`` was DELETED. The replacement
        is ``manager.enqueue_message`` (pinned in the test below).
        """
        from daemon.manager import InstanceManager

        assert hasattr(InstanceManager, "send_message")
        assert callable(getattr(InstanceManager, "send_message"))

    def test_enqueue_message_exists_and_callable(self):
        """enqueue_message method should exist and be callable."""
        from daemon.manager import InstanceManager

        assert hasattr(InstanceManager, "enqueue_message")
        assert callable(getattr(InstanceManager, "enqueue_message"))

    def test_terminate_instance_exists_and_callable(self):
        """terminate_instance method should exist and be callable."""
        from daemon.manager import InstanceManager

        assert hasattr(InstanceManager, "terminate_instance")
        assert callable(getattr(InstanceManager, "terminate_instance"))

    def test_cancel_exists_and_callable(self):
        """cancel method should exist and be callable."""
        from daemon.manager import InstanceManager

        assert hasattr(InstanceManager, "cancel")
        assert callable(getattr(InstanceManager, "cancel"))

    def test_get_instance_exists_and_callable(self):
        """get_instance method should exist and be callable."""
        from daemon.manager import InstanceManager

        assert hasattr(InstanceManager, "get_instance")
        assert callable(getattr(InstanceManager, "get_instance"))

    def test_list_instances_exists_and_callable(self):
        """list_instances method should exist and be callable."""
        from daemon.manager import InstanceManager

        assert hasattr(InstanceManager, "list_instances")
        assert callable(getattr(InstanceManager, "list_instances"))

    def test_get_instance_info_exists_and_callable(self):
        """get_instance_info method should exist and be callable."""
        from daemon.manager import InstanceManager

        assert hasattr(InstanceManager, "get_instance_info")
        assert callable(getattr(InstanceManager, "get_instance_info"))

    def test_get_messages_exists_and_callable(self):
        """get_messages method should exist and be callable."""
        from daemon.manager import InstanceManager

        assert hasattr(InstanceManager, "get_messages")
        assert callable(getattr(InstanceManager, "get_messages"))

    def test_get_queue_stats_exists_and_callable(self):
        """get_queue_stats method should exist and be callable."""
        from daemon.manager import InstanceManager

        assert hasattr(InstanceManager, "get_queue_stats")
        assert callable(getattr(InstanceManager, "get_queue_stats"))

    def test_cancel_instance_requests_exists_and_callable(self):
        """cancel_instance_requests method should exist and be callable."""
        from daemon.manager import InstanceManager

        assert hasattr(InstanceManager, "cancel_instance_requests")
        assert callable(getattr(InstanceManager, "cancel_instance_requests"))

    def test_get_active_requests_exists_and_callable(self):
        """get_active_requests method should exist and be callable."""
        from daemon.manager import InstanceManager

        assert hasattr(InstanceManager, "get_active_requests")
        assert callable(getattr(InstanceManager, "get_active_requests"))


class TestModuleLevelFunctions:
    """Test that module-level functions are still importable from daemon.manager.

    These functions were part of the original manager.py and should remain
    importable from the module level for backward compatibility.
    """

    def test_build_message_content_importable(self):
        """_build_message_content should be importable from daemon.manager."""
        from daemon.manager import _build_message_content

        assert callable(_build_message_content)

    def test_extract_project_keywords_importable(self):
        """extract_project_keywords should be importable from daemon.manager."""
        from daemon.manager import extract_project_keywords

        assert callable(extract_project_keywords)

    def test_get_message_event_type_importable(self):
        """_get_message_event_type should be importable from daemon.manager."""
        from daemon.manager import _get_message_event_type

        assert callable(_get_message_event_type)

    def test_compute_message_content_hash_importable(self):
        """_compute_message_content_hash should be importable from daemon.manager."""
        from daemon.manager import _compute_message_content_hash

        assert callable(_compute_message_content_hash)

    def test_extract_project_keywords_functionality(self):
        """extract_project_keywords should return a list when given input."""
        from daemon.manager import extract_project_keywords

        result = extract_project_keywords("test project")
        assert isinstance(result, list)

    def test_build_message_content_returns_string(self):
        """_build_message_content should return string when no images."""
        from daemon.manager import _build_message_content

        result = _build_message_content("hello", None)
        assert result == "hello"

    def test_build_message_content_returns_list_with_images(self):
        """_build_message_content should return list when images provided."""
        from daemon.manager import _build_message_content

        result = _build_message_content("hello", ["data:image/png;base64,abc123"])
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0] == {"type": "text", "text": "hello"}
        assert result[1]["type"] == "image_url"


class TestInnerClasses:
    """Test that inner classes are still importable from daemon.manager.

    These callback handlers and result types should remain accessible
    for backward compatibility.
    """

    def test_activity_callback_handler_is_class(self):
        """ActivityCallbackHandler should be a class."""
        from daemon.manager import ActivityCallbackHandler

        assert inspect.isclass(ActivityCallbackHandler)

    def test_cancellation_callback_handler_is_class(self):
        """CancellationCallbackHandler should be a class."""
        from daemon.manager import CancellationCallbackHandler

        assert inspect.isclass(CancellationCallbackHandler)

    def test_message_result_is_class(self):
        """MessageResult should be a class."""
        from daemon.manager import MessageResult

        assert inspect.isclass(MessageResult)

    def test_async_message_result_is_class(self):
        """AsyncMessageResult should be a class."""
        from daemon.manager import AsyncMessageResult

        assert inspect.isclass(AsyncMessageResult)

    def test_message_result_can_be_instantiated(self):
        """MessageResult should be instantiable."""
        from daemon.manager import MessageResult

        result = MessageResult(
            content="test content",
            thinking="test thinking",
            tool_calls=[],
        )
        assert result.content == "test content"
        assert result.thinking == "test thinking"
        assert result.tool_calls == []

    def test_async_message_result_can_be_instantiated(self):
        """AsyncMessageResult should be instantiable with required fields."""
        from daemon.manager import AsyncMessageResult

        result = AsyncMessageResult(
            message_id="msg-123",
            instance_id="instance-456",
            status="queued",
        )
        assert result.message_id == "msg-123"
        assert result.instance_id == "instance-456"
        assert result.status == "queued"


class TestServiceDI:
    """Test that services receive manager reference for cross-service access.

    Services that need to access other services or manager state should
    receive a manager reference during initialization.
    """

    def test_cancellation_service_has_manager_reference(self):
        """CancellationService should have _manager attribute."""
        from daemon.services.cancellation import CancellationService

        mock_manager = MagicMock()
        service = CancellationService(manager=mock_manager)

        assert hasattr(service, "_manager")
        assert service._manager is mock_manager

    def test_instance_messaging_service_has_manager_reference(self):
        """InstanceMessagingService should have _manager attribute."""
        from daemon.services.instance_messaging import InstanceMessagingService

        mock_manager = MagicMock()
        mock_cancel = MagicMock()
        mock_child = MagicMock()
        mock_events = MagicMock()

        service = InstanceMessagingService(
            manager=mock_manager,
            cancellation_service=mock_cancel,
            child_reports_service=mock_child,
            events_service=mock_events,
        )

        assert hasattr(service, "_manager")
        assert service._manager is mock_manager

    def test_instance_lifecycle_service_has_manager_reference(self):
        """InstanceLifecycleService should have _manager attribute."""
        from daemon.services.instance_lifecycle import InstanceLifecycleService

        mock_manager = MagicMock()
        mock_cancel = MagicMock()
        mock_events = MagicMock()
        mock_queue = MagicMock()

        service = InstanceLifecycleService(
            manager=mock_manager,
            cancellation_service=mock_cancel,
            events_service=mock_events,
            job_queue_service=mock_queue,
        )

        assert hasattr(service, "_manager")
        assert service._manager is mock_manager

    def test_child_reports_service_has_manager_reference(self):
        """ChildReportsService should have _manager attribute."""
        from daemon.services.child_reports import ChildReportsService

        mock_manager = MagicMock()
        mock_events = MagicMock()

        service = ChildReportsService(
            manager=mock_manager,
            events_service=mock_events,
        )

        assert hasattr(service, "_manager")
        assert service._manager is mock_manager

    def test_error_reporting_service_has_manager_reference(self):
        """ErrorReportingService should have _manager attribute."""
        from daemon.services.error_reporting import ErrorReportingService

        mock_manager = MagicMock()
        mock_events = MagicMock()

        service = ErrorReportingService(
            manager=mock_manager,
            events_service=mock_events,
        )

        assert hasattr(service, "_manager")
        assert service._manager is mock_manager

    def test_title_generation_service_has_manager_reference(self):
        """TitleGenerationService should have _manager attribute."""
        from daemon.services.title_generation import TitleGenerationService

        mock_manager = MagicMock()
        mock_logger = MagicMock()

        service = TitleGenerationService(
            manager=mock_manager,
            logger=mock_logger,
        )

        assert hasattr(service, "_manager")
        assert service._manager is mock_manager

    def test_event_publisher_service_has_manager_reference(self):
        """EventPublisherService should have _manager attribute."""
        from daemon.services.event_publisher import EventPublisherService

        mock_manager = MagicMock()

        service = EventPublisherService(manager=mock_manager)

        assert hasattr(service, "_manager")
        assert service._manager is mock_manager


class TestFuzzyMatching:
    """Test fuzzy matching functionality is available from both locations.

    Verifies that find_near_instance and edit_distance exist in the
    expected locations for backward compatibility.
    """

    def test_find_near_instance_from_utils(self):
        """find_near_instance should be importable from daemon.utils."""
        from daemon.utils import find_near_instance

        assert callable(find_near_instance)

    def test_find_near_instance_from_manager(self):
        """find_near_instance should be importable from daemon.manager."""
        from daemon.manager import find_near_instance

        assert callable(find_near_instance)

    def test_find_near_instance_is_same_function(self):
        """find_near_instance from both modules should be the same function."""
        from daemon.manager import find_near_instance as from_manager
        from daemon.utils import find_near_instance as from_utils

        assert from_manager is from_utils, (
            "find_near_instance should be the same function in both modules"
        )

    def test_edit_distance_exists_in_utils(self):
        """edit_distance should exist in daemon.utils."""
        from daemon.utils import edit_distance

        assert callable(edit_distance)

    def test_edit_distance_functionality(self):
        """edit_distance should calculate correct edit distance."""
        from daemon.utils import edit_distance

        # Same strings have distance 0
        assert edit_distance("hello", "hello") == 0

        # One character difference
        assert edit_distance("hello", "hallo") == 1

        # Different length
        assert edit_distance("hello", "helloo") == 1

        # Completely different
        result = edit_distance("abc", "xyz")
        assert result > 0


class TestCancellationServiceUsage:
    """Test that CancellationService uses proper delegation pattern.

    Verifies CancellationService does NOT directly access private attributes
    like _active_requests, but instead uses proper methods or delegation.
    """

    def test_cancellation_service_uses_get_active_for_instance(self):
        """CancellationService should use get_active_for_instance, not _active_requests."""
        from daemon.services.cancellation import CancellationService
        from unittest.mock import MagicMock

        mock_manager = MagicMock()
        mock_manager._request_registry.get_active_for_instance.return_value = ["msg-1", "msg-2"]

        service = CancellationService(manager=mock_manager)

        # Call cancel_instance_requests which internally uses get_active_for_instance
        result = service.cancel_instance_requests("instance-123", MagicMock())

        # Verify the method was called correctly
        mock_manager._request_registry.get_active_for_instance.assert_called_once_with("instance-123")

    def test_cancellation_service_get_active_requests(self):
        """get_active_requests should use registry's get_active_for_instance."""
        from daemon.services.cancellation import CancellationService
        from unittest.mock import MagicMock

        mock_manager = MagicMock()
        mock_manager._request_registry.get_active_for_instance.return_value = ["msg-1"]

        service = CancellationService(manager=mock_manager)

        result = service.get_active_requests("instance-123")

        assert result == ["msg-1"]
        mock_manager._request_registry.get_active_for_instance.assert_called_once_with("instance-123")

    def test_cancellation_service_does_not_access_active_requests_directly(self):
        """CancellationService should not have direct access to _active_requests attribute."""
        from daemon.services.cancellation import CancellationService

        # CancellationService should NOT have _active_requests as a direct attribute
        # It should access it through self._request_registry property

        mock_manager = MagicMock()
        service = CancellationService(manager=mock_manager)

        # The service should use _request_registry property, not _active_requests
        assert hasattr(service, "_request_registry")
        # It should NOT have _active_requests as a direct attribute
        assert not hasattr(service, "_active_requests")


class TestTitleGenerationHeaders:
    """Test that TitleGenerationService uses correct default_headers.

    Verifies that title generation includes the expected proxy-app header
    for identifying traffic source.
    """

    def test_title_generation_service_exists(self):
        """TitleGenerationService should be importable."""
        from daemon.services.title_generation import TitleGenerationService

        assert issubclass(TitleGenerationService, object)

    def test_title_generation_service_has_generate_method(self):
        """TitleGenerationService should have _generate_and_broadcast_title method."""
        from daemon.services.title_generation import TitleGenerationService

        mock_manager = MagicMock()
        mock_logger = MagicMock()
        service = TitleGenerationService(manager=mock_manager, logger=mock_logger)

        assert hasattr(service, "_generate_and_broadcast_title")
        assert callable(service._generate_and_broadcast_title)

    def test_title_generation_service_config_property(self):
        """TitleGenerationService should access config through manager."""
        from daemon.services.title_generation import TitleGenerationService

        mock_manager = MagicMock()
        mock_manager.config = MagicMock()
        mock_logger = MagicMock()
        service = TitleGenerationService(manager=mock_manager, logger=mock_logger)

        # Service should have _config property that accesses manager.config
        assert hasattr(service, "_config")
        # Access the property
        config = service._config
        assert config is mock_manager.config


class TestNoCircularImports:
    """Test that all service files can be imported without circular import errors.

    Verifies that the decomposition didn't introduce circular dependencies.
    """

    def test_cancellation_service_imports_cleanly(self):
        """CancellationService should import without errors."""
        from daemon.services.cancellation import CancellationService

        assert CancellationService is not None

    def test_event_publisher_service_imports_cleanly(self):
        """EventPublisherService should import without errors."""
        from daemon.services.event_publisher import EventPublisherService

        assert EventPublisherService is not None

    def test_title_generation_service_imports_cleanly(self):
        """TitleGenerationService should import without errors."""
        from daemon.services.title_generation import TitleGenerationService

        assert TitleGenerationService is not None

    def test_child_reports_service_imports_cleanly(self):
        """ChildReportsService should import without errors."""
        from daemon.services.child_reports import ChildReportsService

        assert ChildReportsService is not None

    def test_error_reporting_service_imports_cleanly(self):
        """ErrorReportingService should import without errors."""
        from daemon.services.error_reporting import ErrorReportingService

        assert ErrorReportingService is not None

    def test_instance_messaging_service_imports_cleanly(self):
        """InstanceMessagingService should import without errors."""
        from daemon.services.instance_messaging import InstanceMessagingService

        assert InstanceMessagingService is not None

    def test_instance_lifecycle_service_imports_cleanly(self):
        """InstanceLifecycleService should import without errors."""
        from daemon.services.instance_lifecycle import InstanceLifecycleService

        assert InstanceLifecycleService is not None

    def test_services_package_imports_cleanly(self):
        """daemon.services package should import without errors."""
        from daemon.services import (
            CancellationService,
            EventPublisherService,
            TitleGenerationService,
            ChildReportsService,
            ErrorReportingService,
            InstanceMessagingService,
            InstanceLifecycleService,
        )

        assert CancellationService is not None
        assert EventPublisherService is not None
        assert TitleGenerationService is not None
        assert ChildReportsService is not None
        assert ErrorReportingService is not None
        assert InstanceMessagingService is not None
        assert InstanceLifecycleService is not None


class TestServiceFilesExist:
    """Test that all 7 service files exist and have correct classes.

    Verifies the physical file structure matches expectations.
    """

    def test_cancellation_service_file_exists(self):
        """daemon/services/cancellation.py should exist with CancellationService."""
        import os
        from pathlib import Path

        service_file = Path(__file__).parent.parent.parent / "daemon" / "services" / "cancellation.py"
        assert service_file.exists(), f"Service file not found: {service_file}"

        from daemon.services.cancellation import CancellationService
        assert CancellationService is not None

    def test_event_publisher_service_file_exists(self):
        """daemon/services/event_publisher.py should exist with EventPublisherService."""
        from pathlib import Path

        service_file = Path(__file__).parent.parent.parent / "daemon" / "services" / "event_publisher.py"
        assert service_file.exists(), f"Service file not found: {service_file}"

        from daemon.services.event_publisher import EventPublisherService
        assert EventPublisherService is not None

    def test_title_generation_service_file_exists(self):
        """daemon/services/title_generation.py should exist with TitleGenerationService."""
        from pathlib import Path

        service_file = Path(__file__).parent.parent.parent / "daemon" / "services" / "title_generation.py"
        assert service_file.exists(), f"Service file not found: {service_file}"

        from daemon.services.title_generation import TitleGenerationService
        assert TitleGenerationService is not None

    def test_child_reports_service_file_exists(self):
        """daemon/services/child_reports.py should exist with ChildReportsService."""
        from pathlib import Path

        service_file = Path(__file__).parent.parent.parent / "daemon" / "services" / "child_reports.py"
        assert service_file.exists(), f"Service file not found: {service_file}"

        from daemon.services.child_reports import ChildReportsService
        assert ChildReportsService is not None

    def test_error_reporting_service_file_exists(self):
        """daemon/services/error_reporting.py should exist with ErrorReportingService."""
        from pathlib import Path

        service_file = Path(__file__).parent.parent.parent / "daemon" / "services" / "error_reporting.py"
        assert service_file.exists(), f"Service file not found: {service_file}"

        from daemon.services.error_reporting import ErrorReportingService
        assert ErrorReportingService is not None

    def test_instance_messaging_service_file_exists(self):
        """daemon/services/instance_messaging.py should exist with InstanceMessagingService."""
        from pathlib import Path

        service_file = Path(__file__).parent.parent.parent / "daemon" / "services" / "instance_messaging.py"
        assert service_file.exists(), f"Service file not found: {service_file}"

        from daemon.services.instance_messaging import InstanceMessagingService
        assert InstanceMessagingService is not None

    def test_instance_lifecycle_service_file_exists(self):
        """daemon/services/instance_lifecycle.py should exist with InstanceLifecycleService."""
        from pathlib import Path

        service_file = Path(__file__).parent.parent.parent / "daemon" / "services" / "instance_lifecycle.py"
        assert service_file.exists(), f"Service file not found: {service_file}"

        from daemon.services.instance_lifecycle import InstanceLifecycleService
        assert InstanceLifecycleService is not None


class TestFacadeDelegationPattern:
    """Test that facade methods correctly delegate to service methods.

    Verifies the delegation pattern by checking method signatures match
    and services are called correctly.
    """

    def test_manager_spawn_instance_delegates_to_lifecycle_service(self):
        """spawn_instance should delegate to _lifecycle_service."""
        from daemon.manager import InstanceManager

        manager = InstanceManager.__new__(InstanceManager)
        manager._lifecycle_service = MagicMock()
        manager._lifecycle_service.spawn_instance.return_value = "test-instance-id"

        result = manager.spawn_instance(agent_id="test-agent")

        manager._lifecycle_service.spawn_instance.assert_called_once()
        assert result == "test-instance-id"

    @pytest.mark.skip(
        reason="T6b / D7 LOCKED 2026-08-30: Manager.send_message and "
        "InstanceMessagingService.send_message were DELETED. Awaiting "
        "Phase-2 rewrite."
    )
    def test_manager_send_message_delegates_to_messaging_service(self):
        """send_message should delegate to _messaging_service.

        wc-wake-report-integrity (T6b, D7 LOCKED 2026-08-30): the
        ``Manager.send_message`` (and the corresponding
        ``_messaging_service.send_message``) were DELETED. This test
        asserts the deleted delegation; it is skipped pending a
        Phase-2 rewrite.
        """
        from daemon.manager import InstanceManager

        manager = InstanceManager.__new__(InstanceManager)
        manager._messaging_service = AsyncMock()
        mock_result = MagicMock()
        manager._messaging_service.send_message = AsyncMock(return_value=mock_result)

        async def test():
            result = await manager.send_message("instance-123", "hello")
            assert result is mock_result
            manager._messaging_service.send_message.assert_called_once_with(
                "instance-123", "hello"
            )

        import asyncio
        asyncio.run(test())

    def test_manager_terminate_instance_delegates_to_lifecycle_service(self):
        """terminate_instance should delegate to _lifecycle_service.

        Phase 2 (TD-3/TD-4): the manager facade now passes
        ``terminal_reason`` through to the lifecycle service so the
        watchover 3-strike discriminator reaches the JobItem column.
        """
        from daemon.manager import InstanceManager

        manager = InstanceManager.__new__(InstanceManager)
        manager._lifecycle_service = AsyncMock()
        manager._lifecycle_service.terminate_instance = AsyncMock(return_value=True)

        async def test():
            result = await manager.terminate_instance("instance-123")
            assert result is True
            manager._lifecycle_service.terminate_instance.assert_called_once_with(
                "instance-123", terminal_reason="aborted"
            )

        import asyncio
        asyncio.run(test())

    def test_manager_pause_instance_cascade_delegates_to_lifecycle_service(self):
        """pause_instance_cascade should delegate to _lifecycle_service."""
        from daemon.manager import InstanceManager

        manager = InstanceManager.__new__(InstanceManager)
        manager._lifecycle_service = AsyncMock()
        manager._lifecycle_service.pause_instance_cascade = AsyncMock(
            return_value={
                "paused_ids": ["instance-123", "child-1"],
                "skipped_ids": ["child-2"]
            }
        )

        async def test():
            result = await manager.pause_instance_cascade("instance-123")
            assert result["paused_ids"] == ["instance-123", "child-1"]
            assert result["skipped_ids"] == ["child-2"]
            manager._lifecycle_service.pause_instance_cascade.assert_called_once_with(
                "instance-123", suspension_reason=None
            )

        import asyncio
        asyncio.run(test())

    def test_manager_cancel_delegates_to_cancellation_service(self):
        """cancel should delegate to _cancellation_service."""
        from daemon.manager import InstanceManager
        from daemon.cancellation import CancellationReason

        manager = InstanceManager.__new__(InstanceManager)
        manager._cancellation_service = MagicMock()
        manager._cancellation_service.cancel.return_value = True

        reason = CancellationReason.MANUAL
        result = manager.cancel("msg-123", reason)

        assert result is True
        manager._cancellation_service.cancel.assert_called_once_with("msg-123", reason)

    def test_manager_get_messages_delegates_to_messaging_service(self):
        """get_messages should delegate to _messaging_service."""
        from daemon.manager import InstanceManager

        manager = InstanceManager.__new__(InstanceManager)
        manager._messaging_service = AsyncMock()
        mock_messages = [{"role": "user", "content": "hello"}]
        manager._messaging_service.get_messages = AsyncMock(return_value=mock_messages)

        async def test():
            result = await manager.get_messages("instance-123")
            assert result == mock_messages
            manager._messaging_service.get_messages.assert_called_once_with("instance-123")

        import asyncio
        asyncio.run(test())

    @pytest.mark.asyncio
    async def test_manager_get_queue_stats_delegates_to_messaging_service(self):
        """get_queue_stats should delegate to _messaging_service."""
        from daemon.manager import InstanceManager

        manager = InstanceManager.__new__(InstanceManager)
        manager._messaging_service = AsyncMock()
        mock_stats = {"pending_count": 5, "processing_count": 1}
        manager._messaging_service.get_queue_stats.return_value = mock_stats

        result = await manager.get_queue_stats("instance-123")

        assert result == mock_stats
        manager._messaging_service.get_queue_stats.assert_called_once_with("instance-123")
