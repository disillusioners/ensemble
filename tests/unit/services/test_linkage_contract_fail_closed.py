"""Tests for Fix A — fail-closed work_id on the job-driven dispatch path.

Companion to ``daemon/services/messaging_types.py`` and the four
job-driven call sites in ``daemon/services/job_feedback_observer.py``
and ``daemon/services/job_processor.py``.

Fix A contract (constitution Phase 0, approach-comparison.md row A):

  * A dispatch driven by a JobItem must be structurally unable to
    omit ``work_id`` (the Task's ``work_id`` must equal the JobItem's
    ``job_id`` by construction).
  * Internal paths (agent-to-agent send_message, cascade-resume,
    child reports — no JobItem) legitimately self-mint and the
    default ``work_id_required=False`` keeps them working unchanged.
  * ``_assert_linkage_contract`` is escalated from WARN-only to
    enforcement (``enforce=True``) on the four job-driven call sites:
    the result of any dispatch whose ``job_id`` does NOT match the
    driving JobItem's ``job_id`` raises :class:`LinkageContractError`
    instead of silently warning.

These tests use pure-Python unit-test mechanics — no DB, no manager,
no asyncio loop. The integration coverage lives in the existing
``tests/integration/test_pause_race_*`` family and the
``tests/job_queue/test_orphan_active_job_recovery.py`` pack.
"""
from __future__ import annotations

import logging
import uuid
from unittest.mock import MagicMock

import pytest

from daemon.services.messaging_types import (
    AsyncMessageResult,
    LinkageContractError,
    _assert_linkage_contract,
)


# ============================================================
# _assert_linkage_contract — WARN-only default + enforce mode
# ============================================================

def test_linkage_contract_warns_on_mismatch_default() -> None:
    """Default behaviour (enforce=False) — a mismatch logs a WARNING
    and does not raise. This preserves the pre-Fix-A semantics for
    the legacy internal call sites that have not yet migrated.
    """
    result = AsyncMessageResult(
        message_id="m1", instance_id="i1", job_id="WRONG-UUID"
    )
    logger = MagicMock(spec=logging.Logger)

    # Default enforce=False must not raise
    _assert_linkage_contract(result, "EXPECTED-UUID", source="Test", logger=logger)
    logger.warning.assert_called_once()
    msg = logger.warning.call_args[0][0]
    assert "LINKAGE CONTRACT VIOLATION" in msg
    assert "Test" in msg


def test_linkage_contract_passes_silently_on_match() -> None:
    """Match case — no warning, no raise. Both modes.
    """
    result = AsyncMessageResult(
        message_id="m1", instance_id="i1", job_id="SHARED-UUID"
    )
    logger = MagicMock(spec=logging.Logger)

    # Default mode — silent
    _assert_linkage_contract(result, "SHARED-UUID", source="Test", logger=logger)
    logger.warning.assert_not_called()

    # Enforce mode — also silent on match
    _assert_linkage_contract(
        result, "SHARED-UUID", source="Test", logger=logger, enforce=True
    )
    logger.warning.assert_not_called()


def test_linkage_contract_passes_silently_on_none_result() -> None:
    """``None`` result OR ``job_id=None`` → silent return. Same in
    both modes. Guards against the early-return shortcut misfiring.
    """
    logger = MagicMock(spec=logging.Logger)

    _assert_linkage_contract(None, "UUID", source="Test", logger=logger)
    _assert_linkage_contract(
        None, "UUID", source="Test", logger=logger, enforce=True
    )

    no_job_id = AsyncMessageResult(message_id="m1", instance_id="i1", job_id=None)
    _assert_linkage_contract(no_job_id, "UUID", source="Test", logger=logger)
    _assert_linkage_contract(
        no_job_id, "UUID", source="Test", logger=logger, enforce=True
    )

    logger.warning.assert_not_called()


def test_linkage_contract_enforce_raises_on_mismatch() -> None:
    """Fix A escalation — a mismatch on the job-driven path raises
    :class:`LinkageContractError` instead of warning. Closes the
    auto-mint fail-open handle (D4) on the four JOB-DRIVEN call sites.
    """
    result = AsyncMessageResult(
        message_id="m1", instance_id="i1", job_id="WRONG-UUID"
    )
    logger = MagicMock(spec=logging.Logger)

    with pytest.raises(LinkageContractError) as exc_info:
        _assert_linkage_contract(
            result,
            "EXPECTED-UUID",
            source="JobProcessor",
            logger=logger,
            enforce=True,
        )

    err = exc_info.value
    assert err.source == "JobProcessor"
    assert err.expected_job_id == "EXPECTED-UUID"
    assert err.actual_job_id == "WRONG-UUID"
    assert "EXPECTED-UUID"[:8] in str(err)
    assert "WRONG-UUID"[:8] in str(err)
    # No warning on the enforce path (the raise replaces it).
    logger.warning.assert_not_called()


def test_linkage_contract_error_is_runtime_error_subclass() -> None:
    """``LinkageContractError`` subclasses ``RuntimeError`` so callers
    that catch ``RuntimeError`` (the conventional broad-catch in the
    dispatch path) surface it correctly. Same shape as
    :class:`JobStateMachine.InvalidTransitionError` →
    ``ValueError`` (the codebase's existing error-as-runtime pattern).
    """
    assert issubclass(LinkageContractError, RuntimeError)


# ============================================================
# _ensure_work_id_fail_closed — the extracted fail-closed guard,
# exercised directly. It is a module-level pure function (no DB,
# no manager), so the constitutional guarantee — required + None
# raises instead of auto-minting — is tested by INVOCATION, not by
# signature inspection.
# ============================================================

def test_fail_closed_guard_raises_when_required_and_work_id_none() -> None:
    """Fix A boundary, by invocation — when ``work_id_required=True``
    AND ``work_id=None``, the guard must raise
    :class:`LinkageContractError` instead of silently auto-minting a
    fresh UUID (the 2026-08-31 f1-misfire incident: an auto-mint on
    the job-driven path re-keys the Task and breaks Pattern-f1
    ``get_by_work_id`` recovery lookups).
    """
    from daemon.services.instance_messaging import _ensure_work_id_fail_closed

    with pytest.raises(LinkageContractError) as exc_info:
        _ensure_work_id_fail_closed(None, True)

    err = exc_info.value
    assert err.source == "_prepare_enqueued_message"
    assert err.expected_job_id == "<required>"
    assert "auto-mint" in err.actual_job_id
    # The rendered message must name the violation meaningfully.
    assert "LINKAGE CONTRACT VIOLATION" in str(err)


def test_fail_closed_guard_required_with_value_returns_value_unchanged() -> None:
    """Required + explicit ``work_id`` → returned unchanged. The
    caller-supplied linkage (JobItem.job_id) wins and nothing is
    minted or rewritten.
    """
    from daemon.services.instance_messaging import _ensure_work_id_fail_closed

    assert _ensure_work_id_fail_closed("JOB-UUID-1234", True) == "JOB-UUID-1234"


def test_fail_closed_guard_not_required_with_none_self_mints_uuid() -> None:
    """Not required + ``None`` → a freshly minted, non-empty UUID4
    string. The internal self-mint path (agent-to-agent send_message,
    cascade-resume, child reports — no JobItem) is preserved
    byte-for-byte by the extraction.
    """
    from daemon.services.instance_messaging import _ensure_work_id_fail_closed

    minted = _ensure_work_id_fail_closed(None, False)
    assert isinstance(minted, str)
    assert minted
    # A real UUID4 mint, not a sentinel or empty fill.
    parsed = uuid.UUID(minted)
    assert parsed.version == 4


def test_fail_closed_guard_not_required_with_value_returns_value() -> None:
    """Not required + explicit ``work_id`` → returned unchanged."""
    from daemon.services.instance_messaging import _ensure_work_id_fail_closed

    assert _ensure_work_id_fail_closed("INTERNAL-UUID-5678", False) == (
        "INTERNAL-UUID-5678"
    )


def test_prepare_enqueued_message_signature_keeps_backward_compat() -> None:
    """The new ``work_id_required`` parameter is keyword-only and
    defaults to False — the existing API surface is unchanged for
    legacy callers (HTTP route, telegram, scheduler, internal reports).
    """
    from daemon.services.instance_messaging import InstanceMessagingService
    import inspect

    # enqueue_message signature
    em_sig = inspect.signature(InstanceMessagingService.enqueue_message)
    assert "work_id_required" in em_sig.parameters
    assert em_sig.parameters["work_id_required"].default is False
    assert em_sig.parameters["work_id_required"].kind == inspect.Parameter.KEYWORD_ONLY

    # _prepare_enqueued_message signature
    prep_sig = inspect.signature(InstanceMessagingService._prepare_enqueued_message)
    assert "work_id_required" in prep_sig.parameters
    assert prep_sig.parameters["work_id_required"].default is False
    assert prep_sig.parameters["work_id_required"].kind == inspect.Parameter.KEYWORD_ONLY


# ============================================================
# _assert_linkage_contract — every repaired mint site still satisfies
# linkage. The four repaired sites are:
#   * job_feedback_observer.py:3879 (passes work_id=started_job.job_id)
#   * job_processor.py:975       (passes work_id=proc_job.job_id)
#   * job_processor.py:1065      (passes work_id=proc_job.job_id)
#   * job_processor.py:1291      (passes work_id=job.job_id)
# enqueue_message_job is structurally safe by construction (always mints
# locally and binds the SAME local — verified in instance_messaging.py
# step-2 / step-3 sequence).
# ============================================================

def test_repaired_observer_site_passes_work_id() -> None:
    """job_feedback_observer.py:3879 — the f1-misfire fix site must
    continue to pass ``work_id=started_job.job_id`` post-Fix-A. The
    Fix A boundary just makes that binding structurally guaranteed.
    """
    import inspect
    from daemon.services.job_feedback_observer import JobFeedbackObserver

    # Find the source line of the enqueue_message call inside the
    # observer's _trigger_next_job method and confirm it passes
    # both ``work_id=`` and ``work_id_required=True``.
    src = inspect.getsource(JobFeedbackObserver._trigger_next_job)
    assert "work_id=started_job.job_id" in src, (
        "f1-misfire fix regressed — observer must pass work_id"
    )
    assert "work_id_required=True" in src, (
        "Fix A escalation regressed — observer must set work_id_required=True"
    )
    assert "enforce=True" in src, (
        "Fix A escalation regressed — observer must enforce linkage"
    )


def test_repaired_job_processor_main_dispatch_site_passes_work_id() -> None:
    """job_processor.py main TASK dispatch (line ~1291) — must
    continue to pass ``work_id=job.job_id`` and the Fix A flag.
    """
    import inspect
    from daemon.services.job_processor import JobProcessor

    src = inspect.getsource(JobProcessor._process_next_job)
    assert "work_id=job.job_id" in src, (
        "Fix A regressed — main TASK dispatch must pass work_id"
    )
    assert "work_id_required=True" in src
    # enforce=True must appear at least 3 times (one per fixed site
    # in _process_next_job — the orphan-recovery re-spawn, the
    # orphan-resume re-spawn, and the main TASK dispatch).
    assert src.count("enforce=True") >= 3, (
        f"Expected ≥3 enforce=True calls in JobProcessor._process_next_job, "
        f"got {src.count('enforce=True')}"
    )


def test_repaired_job_processor_crash_recovery_site_passes_work_id() -> None:
    """job_processor.py crash-recovery re-spawn site — must pass
    ``work_id=proc_job.job_id`` + Fix A flag.
    """
    import inspect
    from daemon.services.job_processor import JobProcessor

    src = inspect.getsource(JobProcessor._process_next_job)
    # Two proc_job.job_id sites (crash-recovery + orphan-resume)
    proc_count = src.count("work_id=proc_job.job_id")
    assert proc_count >= 2, (
        f"Expected ≥2 work_id=proc_job.job_id sites in "
        f"JobProcessor._process_next_job, got {proc_count}"
    )
    proc_required_count = src.count("work_id=proc_job.job_id,\n                                    work_id_required=True") + \
                        src.count("work_id=proc_job.job_id,\n                            work_id_required=True")
    assert proc_required_count >= 2


def test_enqueue_message_job_structurally_safe() -> None:
    """enqueue_message_job is structurally safe by construction — the
    shared linkage UUID is minted locally and bound to BOTH the Task
    row's ``work_id`` AND the JobItem's ``job_id`` from the same local.
    Fix A adds ``work_id_required=True`` to make the contract explicit;
    the runtime behaviour is unchanged because the local is
    unconditionally populated.
    """
    import inspect
    from daemon.services.instance_messaging import InstanceMessagingService

    src = inspect.getsource(InstanceMessagingService.enqueue_message_job)
    # Local mint
    assert "job_id = str(uuid.uuid4())" in src, (
        "enqueue_message_job must mint the shared linkage UUID locally"
    )
    # Same local passed as work_id to _prepare_enqueued_message
    prep_idx = src.find("_prepare_enqueued_message")
    assert prep_idx > 0
    assert "work_id=job_id" in src[prep_idx:]
    # Fix A boundary — work_id_required=True on the structurally-safe path
    assert "work_id_required=True" in src
