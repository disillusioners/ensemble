"""f1-misfire gate — behavioral coverage for the JobProcessor tripwire.

The dev observer test (test_observer_warns_on_linkage_contract_violation
in ``tests/job_queue/test_orphan_active_job_recovery.py``) proved the
WARN fired on the observer site pre-Fix-A. This file pins the
**post-Fix-A** contract on the symmetric processor side: the
crash-recovery re-spawn site (``job_processor.py:984``) is a
JOB-DRIVEN dispatch — it passes ``work_id=job_id`` and
``work_id_required=True`` — so a missing ``work_id`` (omission) at
``_prepare_enqueued_message`` (``instance_messaging.py:696``) MUST
RAISE :class:`LinkageContractError` instead of silently re-minting a
fresh UUID (the 2026-08-31 f1-misfire incident).

Two tests in this file — never conflate the two modes in one test:

* ``test_processor_crash_recovery_respawn_raises_on_linkage_omission``
  — JOB-DRIVEN path (``enforce=True``): the omission raise MUST fire
  at the dispatch boundary and the recovery loop MUST finalize the
  JobItem at FAILED (fail-closed — NOT the old WARN-never-fail
  semantics).
* ``test_assert_linkage_contract_warns_on_mismatch_when_not_enforced``
  — non-job-driven (internal) path (``enforce=False``): mismatch
  WARNs and dispatch proceeds (the legacy tripwire semantics
  retained for internal callers that legitimately self-mint).
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from daemon.repositories.job_queue.models import AdmissionState
from daemon.services.instance_messaging import AsyncMessageResult
from daemon.services.job_processor import JobProcessor
from daemon.services.job_queue_service import DemandState
from daemon.services.messaging_types import (
    LinkageContractError,
    _assert_linkage_contract,
)


@pytest.mark.asyncio
async def test_processor_crash_recovery_respawn_raises_on_linkage_omission(caplog):
    """Crash-recovery re-spawn is a JOB-DRIVEN site — ``work_id_required=True``
    + ``enforce=True`` (Fix A). A missing ``work_id`` at
    ``_prepare_enqueued_message`` (the omission path,
    ``instance_messaging.py:696``) RAISES :class:`LinkageContractError`
    with the verbatim omission message; the recovery loop at
    ``job_processor.py:1021`` catches it, logs at ERROR (carrying the
    LinkageContractError str() through its f-string), and finalizes
    the JobItem via ``complete_job(FAILED)`` — fail-closed.

    Old contract (WARN-never-fail, pre-Fix-A): dispatch proceeded on
    mismatch with a WARNING. New contract: dispatch is refused.
    """
    proc_job = SimpleNamespace(
        # Driving JobItem id is set in the test layer so the
        # _assert_linkage_contract mismatch path is unreachable; the
        # mock ``enqueue_message`` simulates the omission raise at
        # ``_prepare_enqueued_message`` directly (the only path that
        # raises before the tripwire sees a ``result``).
        job_id="job-f1-tripfire-1",
        job_type="task",
        instance_id="inst-f1-tripfire-crashed",
        agent_id="developer",
        project_id="proj-f1-tripfire",
        message="drive",
        source="api",
        admission_state=AdmissionState.ACTIVE.value,
    )
    queue = SimpleNamespace(
        queue_id="q",
        project_id="p",
        queue_name="default",
        is_paused=False,
        concurrency_limit=1,
        queue_type="fifo",
    )
    jq = MagicMock()
    jq._repository = MagicMock()
    jq._repository.list_pending_by_queue = MagicMock(return_value=[])
    jq._repository.list_by_queue = MagicMock(return_value=([proc_job], 1))
    # ``complete_job`` is ``async def`` (job_queue_service.py:3207) —
    # MagicMock would raise ``TypeError: object MagicMock can't be used
    # in 'await' expression`` at job_processor.py:1026. AsyncMock makes
    # it await-safe (the original failure mode at HEAD).
    jq.complete_job = AsyncMock(return_value=None)

    mgr = MagicMock()
    # Crash-recovery shape: instance NOT in memory (KeyError) but its
    # DB row is alive + non-terminal — drives the re-spawn branch at
    # job_processor.py:960.
    mgr.get_instance = AsyncMock(side_effect=KeyError("inst-f1-tripfire-crashed"))
    mgr._instance_repository = MagicMock()
    mgr._instance_repository.get = MagicMock(
        return_value=SimpleNamespace(
            instance_id="inst-f1-tripfire-crashed", status="running",
        )
    )
    mgr.spawn_instance_with_mcp = AsyncMock(
        return_value="inst-f1-tripfire-crashed"
    )
    # Simulate the omission raise at instance_messaging.py:696 —
    # ``work_id_required=True`` + missing ``work_id`` raises
    # ``LinkageContractError(omission=True)`` with the verbatim M6
    # omission message (messaging_types.py:71-82).
    mgr.enqueue_message = AsyncMock(
        side_effect=LinkageContractError(
            source="_prepare_enqueued_message",
            expected_job_id="",
            actual_job_id="",
            omission=True,
        )
    )

    project_repo = MagicMock(
        get=MagicMock(return_value=SimpleNamespace(job_queue_paused=False))
    )
    queue_repo = MagicMock(
        list_queues_with_admittable_work=MagicMock(return_value=[queue])
    )
    proc = JobProcessor(
        queue_service=jq,
        instance_manager=mgr,
        project_repo=project_repo,
        queue_repo=queue_repo,
        poll_interval=0.1,
    )

    with caplog.at_level(logging.ERROR, logger="daemon.services.job_processor"):
        # The crash-recovery try/except at job_processor.py:1021
        # catches the LinkageContractError, logs at ERROR (carrying
        # ``str(e)`` — the verbatim omission message), and finalizes
        # the JobItem via ``complete_job(FAILED)``. No
        # ``pytest.raises`` here — the recovery path MUST absorb and
        # finalize (this is the documented fail-closed behavior; a
        # silent dispatch-pass would be the old WARN-never-fail bug).
        await proc._process_next_job()

    # 1. The verbatim Fix A omission substring MUST appear in the
    #    captured ERROR log — the f-string at job_processor.py:1024
    #    interpolates ``{e}`` which is the LinkageContractError whose
    #    str() carries the omission message.
    recovered = [
        r
        for r in caplog.records
        if r.levelno >= logging.ERROR
        and "job-driven dispatch arrived with work_id=None" in r.getMessage()
    ]
    assert recovered, (
        "crash-recovery must propagate the Fix A omission message at "
        "ERROR level (the verbatim substring 'job-driven dispatch "
        "arrived with work_id=None' must appear). Got: "
        f"{[r.getMessage() for r in caplog.records]}"
    )

    # 2. The recovery path MUST finalize the JobItem via
    #    ``complete_job`` with ``demand_state=FAILED`` and the verbatim
    #    omission message in the ``error`` kwarg — fail-closed. The
    #    old contract (WARN-only, dispatch proceeds) would not call
    #    ``complete_job`` at all from the tripwire.
    assert jq.complete_job.await_count == 1, (
        "crash-recovery must finalize the JobItem on LinkageContractError "
        "(fail-closed — NOT the old WARN-never-fail semantics)"
    )
    cj_kwargs = jq.complete_job.call_args.kwargs
    assert cj_kwargs.get("demand_state") == DemandState.FAILED, (
        f"recovery finalization must use DemandState.FAILED; got "
        f"demand_state={cj_kwargs.get('demand_state')!r}"
    )
    assert "job-driven dispatch arrived with work_id=None" in cj_kwargs.get(
        "error", ""
    ), (
        f"recovery finalization must carry the verbatim omission message "
        f"in the error kwarg; got error={cj_kwargs.get('error')!r}"
    )

    # 3. ``enqueue_message`` MUST have been called exactly once and
    #    with the Fix A binding (``work_id_required=True``) — this is
    #    what makes the omission raise structurally reachable at the
    #    real ``_prepare_enqueued_message`` call site. A regression
    #    that drops ``work_id_required=True`` would silently re-mint
    #    and trip neither path (the original D4 fail-open handle).
    assert mgr.enqueue_message.await_count == 1
    em_kwargs = mgr.enqueue_message.call_args.kwargs
    assert em_kwargs.get("work_id_required") is True, (
        f"Fix A: the crash-recovery dispatch must pass "
        f"work_id_required=True so a missing work_id raises at "
        f"_prepare_enqueued_message. Got kwargs: "
        f"{sorted(em_kwargs.keys())}"
    )
    assert em_kwargs.get("work_id") == proc_job.job_id, (
        f"Fix A: the crash-recovery dispatch must pass work_id=job_id "
        f"so the driving Task links to its JobItem. Got "
        f"work_id={em_kwargs.get('work_id')!r}"
    )


@pytest.mark.asyncio
async def test_assert_linkage_contract_warns_on_mismatch_when_not_enforced(caplog):
    """Legacy/internal (non-job-driven) sites use ``enforce=False`` —
    a mismatch must WARN but NOT raise.

    The crash-recovery re-spawn site is now ``enforce=True`` (see
    ``test_processor_crash_recovery_respawn_raises_on_linkage_omission``
    above); this test pins the **symmetric** WARN path on the
    shared tripwire helper so a future Fix A rollout cannot collapse
    the two modes into one. The contract: an internal caller that
    legitimately self-mints (e.g. ``enqueue_message_job`` mint site,
    internal ``send_message``, cascade-resume) keeps the WARN-never-
    fail semantics because it does not own a driving JobItem.

    This is a direct unit test on
    ``daemon.services.messaging_types._assert_linkage_contract`` —
    bypassing the JobProcessor — so the assertion isolates the
    tripwire's two-mode contract from the dispatch wiring.
    """
    result = AsyncMessageResult(
        message_id="m",
        instance_id="inst-legacy",
        status="queued",
        job_id="mismatched-uuid",  # NOT equal to the driving job_id below
    )
    driving_job_id = "driving-job-id"
    logger = logging.getLogger("daemon.services.job_processor")
    with caplog.at_level(logging.WARNING, logger="daemon.services.job_processor"):
        # enforce=False (the default; explicit for clarity) → WARN, no raise.
        _assert_linkage_contract(
            result,
            driving_job_id,
            source="LegacyInternalCaller",
            logger=logger,
            enforce=False,
        )

    viol = [
        r
        for r in caplog.records
        if r.levelno >= logging.WARNING
        and "LINKAGE CONTRACT" in r.getMessage()
        and "LegacyInternalCaller" in r.getMessage()
    ]
    assert viol, (
        f"WARN-mode tripwire must fire on mismatch when enforce=False; "
        f"got: {[r.getMessage() for r in caplog.records]}"
    )
    # And the WARN must carry the mismatch (NOT omission) template —
    # the fix-A escalation only changes omission wording; the legacy
    # mismatch WARN template is byte-identical base↔HEAD. The expected
    # id is truncated to 8 chars in the template (messaging_types.py:150).
    assert "driving-" in viol[0].getMessage()
    assert "mismatc" in viol[0].getMessage()