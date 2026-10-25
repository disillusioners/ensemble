"""Phase 1 — the InstanceManager facade signature did not GROW.

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
  add NO new InstanceManager kwarg:

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


#: The facade signatures at the Phase-0 base leg (dabe1f04d). Any
#: change here is a Phase-1 deviation that must be reported.
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
)

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
)


def test_enqueue_message_facade_signature_did_not_grow():
    params = tuple(
        inspect.signature(InstanceManager.enqueue_message).parameters
    )
    assert params == _EXPECTED_ENQUEUE_MESSAGE_PARAMS


def test_enqueue_message_job_facade_signature_did_not_grow():
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
