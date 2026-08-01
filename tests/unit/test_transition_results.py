from dataclasses import FrozenInstanceError
import pytest
from daemon.services.turn_transitions import TransitionResult

def test_transition_result_shape_and_frozen():
    result = TransitionResult("w", "i", "running", "completed", frozenset({"task"}), (), sse_payload={"event": "done"})
    assert result.work_id == "w"
    assert result.instance_id == "i"
    assert result.mirrors_touched == frozenset({"task"})
    assert result.wakeup_payload is None
    with pytest.raises(FrozenInstanceError):
        result.work_id = "other"
