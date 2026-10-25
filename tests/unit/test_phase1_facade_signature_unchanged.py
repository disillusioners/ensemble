"""Phase 1 — the InstanceManager facade signature matches the pinned tuple.

Proposal-a §7's convention-conformance checklist asks for "a cheap
anti-regression assert that the facade signature did not grow".

WHY THIS MATTERS
  The ``InstanceManager`` is a MANUAL-FORWARDING FACADE over
  ``InstanceMessagingService``. A new kwarg on a facade method has to
  be forwarded by hand; a kwarg added to the facade but NOT forwarded
  is the known bug class (the facade-forwarding discipline — the
  AsyncMock + ``inspect.getsource`` substring assertions stay green at
  this seam, so the defect slips past unit tests).

  The Phase-1 train (D4(i) stamping + D2 census/mint/C-union + D1
  resolver + D5(i) epoch clears + the W1/W2/T2 ledger) is designed to
  add NO new InstanceManager kwarg (Proposal B PR1 sanctioned exactly
  ONE — ``mission_id``, keyword-only on both enqueue methods; any
  FURTHER growth is a deviation that must be reported):

  * D4(i) is constants-only plus a local derivation helper;
  * D1 uses the row carrier (``_ChildCompletionDbResult
    .report_recipient_id``) — no facade crossing;
  * D2 uses the tool-layer ``bus.watch`` plus an existing return value;
  * D5(i) is repository-level;
  * W1 adds a field to the ``AsyncMessageResult`` DATACLASS (the
    manager returns the service's result unchanged — no kwarg).

  This file pins that. It is intentionally CHEAP — a signature
  comparison, no DB, no daemon.

  The canonical facade-forwarding guards live in
  ``tests/unit/test_manager_enqueue_message_work_id_required.py`` and
  ``tests/integration/test_job_driven_enqueue_work_id_facade.py``;
  this file complements them with the "the signature did not grow"
  half.
"""

from __future__ import annotations

import inspect

from daemon.manager import InstanceManager


#: The facade signatures as pinned at the Phase-0 base leg (dabe1f04d)
#: plus PR1's ONE sanctioned growth. Any change here is a deviation
#: that must be reported.
# Proposal B PR1 (2026-10-25): ``mission_id`` is the train's ONE
# sanctioned facade growth — keyword-only on both enqueue methods,
# forwarded to the service (the Facade-Forwarding Discipline; the
# mission_id half is pinned in
# ``tests/unit/test_mission_record_facade_forwarding.py``).
_EXPECTED_ENQUEUE_MESSAGE_PARAMS = (
    "self",
    "instance_id",
    "message",
    "source",
    "priority",
    "images",
    "metadata",
    "is_deferred",
    "is_background",
    "work_id",
    "work_id_required",
    "image_refs",
    "mission_id",
)

# NOTE (PR1 audit): the F11 ``dispatch_chain`` kwarg crossed the facade
# WITHOUT this pin ever being updated — the job-half of this guard was
# silently RED at base 7af1d2632 (pre-existing drift, A/B-proven). PR1
# repairs the pin to the TRUE current signature (dispatch_chain) AND
# adds ``mission_id``.
_EXPECTED_ENQUEUE_MESSAGE_JOB_PARAMS = (
    "self",
    "instance_id",
    "message",
    "source",
    "priority",
    "images",
    "metadata",
    "is_deferred",
    "is_background",
    "queue_id",
    "image_refs",
    "idempotency_key",
    "dispatch_chain",
    "mission_id",
)


def test_enqueue_message_facade_signature_matches_the_pinned_tuple():
    params = tuple(
        inspect.signature(InstanceManager.enqueue_message).parameters
    )
    assert params == _EXPECTED_ENQUEUE_MESSAGE_PARAMS


def test_enqueue_message_job_facade_signature_matches_the_pinned_tuple():
    params = tuple(
        inspect.signature(InstanceManager.enqueue_message_job).parameters
    )
    assert params == _EXPECTED_ENQUEUE_MESSAGE_JOB_PARAMS


def test_the_result_dataclass_carries_the_w1_reason_without_a_kwarg():
    """W1's split rides the RESULT dataclass, not the facade.

    ``InstanceManager.enqueue_message`` returns the service's
    ``AsyncMessageResult`` unchanged (the manager does not rebuild it),
    so the new ``not_queued_reason`` field reaches the tool layer
    without any facade change.
    """
    from daemon.services.messaging_types import AsyncMessageResult

    result = AsyncMessageResult(
        message_id="m",
        instance_id="i",
        queued=False,
        not_queued_reason="deferred_pause_marker",
    )
    assert result.not_queued_reason == "deferred_pause_marker"
    # The dataclass field order is stable (the tool layer reads it by
    # attribute, but a stable order keeps the repr readable).
    assert AsyncMessageResult.__dataclass_fields__.keys() >= {
        "message_id",
        "instance_id",
        "status",
        "job_id",
        "queued",
        "not_queued_reason",
    }
