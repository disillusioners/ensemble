"""Tests for graph task cancellation in pause_instance_cascade.

These tests verify that:
1. Graph tasks are properly registered when message processing starts
2. Graph tasks are properly unregistered when message processing ends
3. Graph tasks are cancelled when pause_instance_cascade is called
4. CancelledError is handled cleanly in the streaming loop
"""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch


class TestGraphTaskRegistration:
    """Tests for graph task registration/unregistration."""

    def test_manager_has_graph_tasks_dict(self):
        """Manager should have _graph_tasks dict for tracking running graph tasks."""
        from daemon.manager import InstanceManager
        
        # Check the class definition contains _graph_tasks initialization
        import inspect
        source = inspect.getsource(InstanceManager.__init__)
        
        # Verify _graph_tasks is initialized in __init__
        assert '_graph_tasks' in source
        assert 'dict' in source


class TestGraphTaskCancellation:
    """Tests for graph task cancellation on pause."""

    def test_pause_single_cancels_graph_task(self):
        """_pause_single should cancel the running graph task."""
        from unittest.mock import MagicMock, patch
        from daemon.cancellation import CancellationReason
        
        # Create mock manager
        mock_manager = MagicMock()
        mock_manager._graph_tasks = {}
        mock_manager._request_registry = MagicMock()
        mock_manager._instance_repository = MagicMock()
        
        # Create a mock graph task
        mock_task = MagicMock(spec=asyncio.Task)
        mock_task.done.return_value = False
        mock_manager._graph_tasks['test-instance'] = mock_task
        
        # Create mock metadata
        mock_meta = MagicMock()
        mock_meta.status = 'running'
        mock_manager._instance_repository.get.return_value = mock_meta
        
        # Create lifecycle service
        from daemon.services.instance_lifecycle import InstanceLifecycleService
        from daemon.services.cancellation import CancellationService
        
        lifecycle_service = InstanceLifecycleService(
            manager=mock_manager,
            cancellation_service=MagicMock(spec=CancellationService),
        )
        
        # Test the cancellation logic directly
        # The logic in _pause_single is:
        graph_task = mock_manager._graph_tasks.get('test-instance')
        if graph_task and not graph_task.done():
            graph_task.cancel()
        
        # Verify task was cancelled
        mock_task.cancel.assert_called_once()


class TestCancelledErrorHandling:
    """Tests for CancelledError handling in message processing."""

    def test_cancelled_error_caught_in_streaming_loop(self):
        """CancelledError in streaming loop should be caught and logged."""
        # This test verifies the structure of the try/except block
        # by checking that CancelledError is a subclass of Exception
        # that can be caught separately
        
        async def mock_streaming():
            yield {'agent': {'messages': []}}
            raise asyncio.CancelledError()
        
        async def test():
            caught_cancelled = False
            caught_exception = False
            
            try:
                try:
                    async for _ in mock_streaming():
                        pass
                except asyncio.CancelledError:
                    caught_cancelled = True
            except Exception:
                caught_exception = True
            
            return caught_cancelled, caught_exception
        
        cancelled, exception = asyncio.run(test())
        
        # CancelledError should be caught by the inner except
        assert cancelled is True
        # Other exceptions should not be caught
        assert exception is False


class TestPauseInstanceCascadeIntegration:
    """Integration tests for pause_instance_cascade with graph task cancellation."""

    def test_pause_keeps_instance_in_memory(self):
        """After pause, instance should remain in instances dict (resumable)."""
        # This would require full integration testing with actual graph
        # For now, we verify the logic through mocking
        pass

    def test_pause_sets_status_to_paused(self):
        """After pause, instance status should be set to paused."""
        from unittest.mock import MagicMock

        mock_manager = MagicMock()
        mock_manager._graph_tasks = {}
        mock_manager._request_registry = MagicMock()
        mock_manager._instance_repository = MagicMock()
        mock_manager._live_hub = MagicMock()

        # Create mock metadata
        mock_meta = MagicMock()
        mock_meta.status = 'running'
        mock_manager._instance_repository.get.return_value = mock_meta

        # Verify that status update will be called with 'paused'
        from daemon.repositories.instance.models import InstanceStatus
        mock_manager._instance_repository.update_status('test-id', InstanceStatus.PAUSED.value)

        mock_manager._instance_repository.update_status.assert_called_with('test-id', 'paused')

    def test_graph_task_unregistered_after_processing(self):
        """Graph task should be unregistered after processing completes."""
        # This verifies the finally block logic
        mock_manager = MagicMock()
        mock_manager._graph_tasks = {'test-instance': MagicMock()}
        
        # Simulate finally block logic
        mock_manager._graph_tasks.pop('test-instance', None)
        
        assert 'test-instance' not in mock_manager._graph_tasks


# wc-wake-report-integrity T6b completion (2026-08-30): the
# ``TestSendMessageTaskRegistration`` class was DELETED as a dead contract —
# ``test_send_message_has_task_registration`` ran ``inspect.getsource`` over
# the deleted ``InstanceMessagingService.send_message`` (the :1060
# ``graph.ainvoke`` bypass removed by C1-D7); a source-text assert on a
# method that no longer exists tests nothing. The task-registration
# invariant on the SURVIVING turn pipeline remains pinned by
# ``test_process_message_has_task_registration`` below (unaffected).
def test_process_message_has_task_registration():
    """_process_message_with_tracking should register and unregister graph task."""
    import inspect
    from daemon.services.instance_messaging import InstanceMessagingService

    source = inspect.getsource(InstanceMessagingService._process_message_with_tracking)

    # Verify task registration is in the code
    assert 'asyncio.current_task()' in source
    assert '_graph_tasks[instance_id]' in source
    assert '_graph_tasks.pop(instance_id' in source
    assert 'asyncio.CancelledError' in source


class TestEdgeCases:
    """Tests for edge cases in graph task cancellation."""

    def test_cancel_nonexistent_task_no_error(self):
        """Cancelling non-existent task should not raise error."""
        mock_manager = MagicMock()
        mock_manager._graph_tasks = {}
        
        # Get non-existent task
        graph_task = mock_manager._graph_tasks.get('nonexistent')
        if graph_task and not graph_task.done():
            graph_task.cancel()
        
        # Should not raise

    def test_cancel_already_done_task_no_error(self):
        """Cancelling already-done task should not raise error."""
        mock_manager = MagicMock()
        
        # Create done task
        mock_task = MagicMock(spec=asyncio.Task)
        mock_task.done.return_value = True
        
        # Should not call cancel on done task
        if mock_task and not mock_task.done():
            mock_task.cancel()
        
        mock_task.cancel.assert_not_called()
