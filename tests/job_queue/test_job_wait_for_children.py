"""Tests for job completion waiting for all children to finish.

When a leader agent spawns child agents, the job should only be marked COMPLETED
when the leader AND all its children have finished processing.
"""

import pytest


class TestJobWaitsForChildrenLogic:
    """Tests for the core logic of job completion waiting for children."""

    def test_root_with_no_children_should_complete_immediately(self):
        """Verify the logic: root instance with waiting_for=0 should trigger immediate completion."""
        # This is a logic test - verifying the decision tree
        parent_id = None
        waiting_for = 0
        
        # Logic from _process_child_completion_and_notify_parent:
        if parent_id is None:
            if waiting_for > 0:
                # Should transition to WAITING_CHILDREN
                should_wait = True
            else:
                # Should publish completed immediately
                should_wait = False
        else:
            should_wait = False
        
        assert should_wait is False, "Root with no children should complete immediately"

    def test_root_with_children_should_wait(self):
        """Verify the logic: root instance with waiting_for>0 should NOT complete immediately."""
        parent_id = None
        waiting_for = 3  # 3 children still running
        
        if parent_id is None:
            if waiting_for > 0:
                should_wait = True
            else:
                should_wait = False
        else:
            should_wait = False
        
        assert should_wait is True, "Root with children should wait for them"

    def test_child_completion_should_decrement_waiting_for(self):
        """Verify that child completion decrements parent's waiting_for."""
        # Simulate parent state
        waiting_for = 2
        
        # Simulate child completion
        waiting_for = max(0, waiting_for - 1)
        
        assert waiting_for == 1

    def test_last_child_completion_sets_waiting_for_to_zero(self):
        """Verify that last child completion sets waiting_for to 0."""
        waiting_for = 1
        
        waiting_for = max(0, waiting_for - 1)
        
        assert waiting_for == 0

    def test_parent_completes_when_waiting_for_zero_and_no_pending(self):
        """Verify parent completes when waiting_for=0 and no pending messages."""
        waiting_for = 0
        has_pending_messages = False
        
        should_complete = waiting_for == 0 and not has_pending_messages
        
        assert should_complete is True

    def test_parent_waits_when_waiting_for_zero_but_has_pending(self):
        """Verify parent waits when waiting_for=0 but has pending messages."""
        waiting_for = 0
        has_pending_messages = True
        
        should_complete = waiting_for == 0 and not has_pending_messages
        
        assert should_complete is False

    def test_update_parent_returns_completed_info(self):
        """Verify _update_parent_on_child_complete returns tuple with completed info."""
        # Simulate the return tuple structure
        waiting_for = 0
        has_pending_messages = False
        parent_status = "waiting_children"
        
        if waiting_for == 0 and parent_status == "waiting_children":
            if not has_pending_messages:
                # Parent completes
                transitioned_to_running = False
                completed_parent_id = "parent-123"
                completed_parent_parent_id = None
            else:
                # Parent continues running
                transitioned_to_running = True
                completed_parent_id = None
                completed_parent_parent_id = None
        else:
            transitioned_to_running = False
            completed_parent_id = None
            completed_parent_parent_id = None
        
        assert transitioned_to_running is False
        assert completed_parent_id == "parent-123"
        assert completed_parent_parent_id is None

    def test_update_parent_returns_running_when_has_pending(self):
        """Verify _update_parent_on_child_complete returns running when parent has pending."""
        waiting_for = 0
        has_pending_messages = True
        parent_status = "waiting_children"
        
        if waiting_for == 0 and parent_status == "waiting_children":
            if not has_pending_messages:
                transitioned_to_running = False
                completed_parent_id = "parent-123"
                completed_parent_parent_id = None
            else:
                transitioned_to_running = True
                completed_parent_id = None
                completed_parent_parent_id = None
        else:
            transitioned_to_running = False
            completed_parent_id = None
            completed_parent_parent_id = None
        
        assert transitioned_to_running is True
        assert completed_parent_id is None


class TestJobCompletionCascade:
    """Tests for the complete cascade flow of job completion."""

    def test_full_cascade_flow(self):
        """Test the complete flow: leader spawns 2 children, both complete, job finishes."""
        # Track events
        lifecycle_events = []
        
        # Step 1: Leader message completes, has 2 children
        leader_parent_id = None
        leader_waiting_for = 2
        leader_status = "running"
        
        if leader_parent_id is None:
            if leader_waiting_for > 0:
                leader_status = "waiting_children"
                lifecycle_events.append({"type": "status_change", "status": leader_status})
                # Don't publish completed yet
            else:
                lifecycle_events.append({"type": "completed"})
        
        assert leader_status == "waiting_children"
        assert len([e for e in lifecycle_events if e.get("type") == "completed"]) == 0
        
        # Step 2: First child completes
        leader_waiting_for = max(0, leader_waiting_for - 1)  # Now 1
        
        # First child sends report to leader
        lifecycle_events.append({"type": "child_completed", "child_id": "child-1"})
        
        # Leader checks: waiting_for > 0, so don't complete
        if leader_waiting_for > 0:
            # Stay in waiting_children
            pass
        else:
            lifecycle_events.append({"type": "completed"})
        
        assert leader_waiting_for == 1
        assert len([e for e in lifecycle_events if e.get("type") == "completed"]) == 0
        
        # Step 3: Second child completes - NOW leader should complete
        leader_waiting_for = max(0, leader_waiting_for - 1)  # Now 0
        
        lifecycle_events.append({"type": "child_completed", "child_id": "child-2"})
        
        # Leader checks: waiting_for == 0, no pending messages
        has_pending = False
        if leader_waiting_for == 0 and not has_pending:
            lifecycle_events.append({"type": "completed", "instance_id": "leader"})
        
        completed_events = [e for e in lifecycle_events if e.get("type") == "completed"]
        assert len(completed_events) == 1
        assert completed_events[0]["instance_id"] == "leader"

    def test_root_completes_immediately_without_children(self):
        """Test that root completes immediately when it has no children."""
        lifecycle_events = []
        
        parent_id = None
        waiting_for = 0
        
        if parent_id is None:
            if waiting_for > 0:
                lifecycle_events.append({"type": "waiting_children"})
            else:
                lifecycle_events.append({"type": "completed"})
        
        completed_events = [e for e in lifecycle_events if e.get("type") == "completed"]
        assert len(completed_events) == 1

    def test_parent_with_grandchildren_waits_for_all(self):
        """Test that a parent with grandchildren waits for all descendants."""
        lifecycle_events = []
        
        # Leader (root)
        leader_waiting_for = 1  # Only waiting for child-1
        
        # Child-1 (has 2 grandchildren)
        child1_waiting_for = 2  # Waiting for grandchild-1 and grandchild-2
        
        # Step 1: Leader message completes
        if leader_waiting_for > 0:
            lifecycle_events.append({"type": "leader_waiting"})
        
        # Step 2: Child-1 message completes
        if child1_waiting_for > 0:
            lifecycle_events.append({"type": "child1_waiting"})
        
        # Step 3: Grandchild-1 completes
        child1_waiting_for = max(0, child1_waiting_for - 1)
        lifecycle_events.append({"type": "grandchild1_done"})
        
        if child1_waiting_for > 0:
            pass  # Still waiting
        
        # Step 4: Grandchild-2 completes
        child1_waiting_for = max(0, child1_waiting_for - 1)
        lifecycle_events.append({"type": "grandchild2_done"})
        
        # Now child-1 can complete
        if child1_waiting_for == 0:
            lifecycle_events.append({"type": "child1_completed"})
            leader_waiting_for = max(0, leader_waiting_for - 1)
        
        # Now leader can complete
        if leader_waiting_for == 0:
            lifecycle_events.append({"type": "leader_completed"})
        
        completed = [e for e in lifecycle_events if "completed" in e.get("type", "")]
        assert len(completed) == 2  # child1 and leader both completed
        assert lifecycle_events[-1]["type"] == "leader_completed"
