"""f1-misfire gate — behavioral coverage for the JobProcessor tripwire.

The dev observer test (test_observer_warns_on_linkage_contract_violation)
proves the WARN fires on the observer site. This file adds the symmetric
processor-side spot: drive ``JobProcessor._process_next_job``'s
crash-recovery re-spawn branch with a mocked ``enqueue_message`` whose
returned ``AsyncMessageResult.job_id`` is MISMATCHED, assert the linkage
WARN fires (source="JobProcessor") and the dispatch does NOT fail
(tripwire is WARN-never-fail semantics, f1-misfire council W1).
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.repositories.job_queue.models import AdmissionState
from daemon.services.instance_messaging import AsyncMessageResult
from daemon.services.job_processor import JobProcessor


@pytest.mark.asyncio
async def test_processor_crash_recovery_respawn_warns_on_linkage_violation(caplog):
    proc_job = SimpleNamespace(
        job_id="job-f1-tripfire-1", job_type="task",
        instance_id="inst-f1-tripfire-crashed", agent_id="developer",
        project_id="proj-f1-tripfire", message="drive", source="api",
        admission_state=AdmissionState.ACTIVE.value,
    )
    queue = SimpleNamespace(queue_id="q", project_id="p", queue_name="default",
                            is_paused=False, concurrency_limit=1, queue_type="fifo")
    jq = MagicMock()
    jq._repository.list_pending_by_queue = MagicMock(return_value=[])
    jq._repository.list_by_queue = MagicMock(return_value=([proc_job], 1))
    mgr = MagicMock()
    mgr.get_instance = AsyncMock(side_effect=KeyError("inst-f1-tripfire-crashed"))
    mgr._instance_repository.get = MagicMock(return_value=SimpleNamespace(
        instance_id="inst-f1-tripfire-crashed", status="running"))
    mgr.spawn_instance_with_mcp = AsyncMock(return_value="inst-f1-tripfire-crashed")
    mgr.enqueue_message = AsyncMock(return_value=AsyncMessageResult(
        message_id="m", instance_id="inst-f1-tripfire-crashed",
        status="queued", job_id="MISMATCHED-fresh-uuid"))
    project_repo = MagicMock(get=MagicMock(return_value=SimpleNamespace(job_queue_paused=False)))
    queue_repo = MagicMock(list_queues_with_admittable_work=MagicMock(return_value=[queue]))
    proc = JobProcessor(queue_service=jq, instance_manager=mgr,
                        project_repo=project_repo, queue_repo=queue_repo, poll_interval=0.1)
    with caplog.at_level(logging.WARNING, logger="daemon.services.job_processor"):
        await proc._process_next_job()
    assert mgr.enqueue_message.await_count == 1, "dispatch must not fail (WARN-never-fail)"
    viol = [r for r in caplog.records if r.levelno >= logging.WARNING
            and "LINKAGE CONTRACT" in r.getMessage() and "JobProcessor" in r.getMessage()]
    assert viol, f"processor tripwire must WARN on mismatch; got: {[r.getMessage() for r in caplog.records]}"
