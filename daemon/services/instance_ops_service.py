"""Dismiss-question service — the body of the dismiss route, extracted.

``POST /instances/{instance_id}/question/dismiss`` used to carry its whole
implementation inside ``daemon/routers/instances.py``. That body is not
importable from a TOOL: it reaches into ``request.app.state.live_hub``,
raises ``HTTPException`` envelopes and runs a ``finally`` block that
releases the recorded question-pause set. A tools module must never import
a router — the dependency would point the wrong way — so the body lives
here and the route delegates to it.

The service is transport-agnostic:

* ``live_hub`` is a PLAIN parameter (may be ``None``) — the same shape
  ``daemon/routers/answer_helper.py::answer_questions_via_instance``
  already uses. The router passes
  ``getattr(request.app.state, "live_hub", None)``; a tool passes
  ``getattr(manager, "_live_hub", None)``.
* failures are reported as TYPED errors (:class:`DismissWritePaused`,
  :class:`DismissInstanceNotFound`, :class:`DismissNoPendingQuestion`,
  :class:`DismissPackNotPending`, :class:`DismissCascadeFailed`), each
  carrying the human text in ``.message``. The router re-maps them onto
  the exact ``HTTPException`` envelopes the route has always raised, so
  the HTTP contract is byte-identical.

ORDERING CONTRACT (F3) — preserved verbatim from the route:

1. The ``question_pack`` SSE event (``status="dismissed"``) is emitted
   BEFORE the resume cascade. The emit is BEST-EFFORT — a failed emit
   must NOT stop the cascade.
2. The dismissal handle is resolved FIRST (while the task is still
   ``PAUSED``) and only THEN the cascade resumes:
   ``resume_processing_job`` routes the dismissal message through
   ``find_suspended_turn_for_answer``, which requires
   ``Task.status == 'paused'``. The cascade would flip the task to
   ``PENDING`` first and the message would lose its route.
3. The recorded side-effect set is released in a ``finally`` — the pack
   is already consumed at that point, so a raising cascade must NOT skip
   the release (a skipped release after a consumed pack IS the stranded
   state).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class InstanceOpsServiceError(Exception):
    """Base for the typed errors the dismiss service raises.

    Attributes:
        message: The human-readable text of the failure. The router
            rebuilds its ``ErrorResponse`` envelope from this string,
            so it must be byte-identical to the text the route used to
            raise inline.
    """

    #: The HTTP status the router maps this error onto.
    status_code: int = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DismissWritePaused(InstanceOpsServiceError):
    """The daemon is write-paused (a migration owns the writes)."""

    status_code = 503


class DismissInstanceNotFound(InstanceOpsServiceError):
    """The instance is unknown to the manager."""

    status_code = 404


class DismissNoPendingQuestion(InstanceOpsServiceError):
    """No question pack exists for the instance."""

    status_code = 404


class DismissPackNotPending(InstanceOpsServiceError):
    """A pack exists but is no longer pending (answered / dismissed)."""

    status_code = 409


class DismissCascadeFailed(InstanceOpsServiceError):
    """The resume cascade raised — the recorded set was still released."""

    status_code = 500


async def _deliberate_pause_exclusions(
    manager: Any,
    instance_id: str,
    pack_id: str | None,
) -> list[str]:
    """The recorded instances a LATER DELIBERATE pause owns (C2/rider).

    The question-lifecycle paths (answer / dismiss / gate supersession)
    cascade-resume the asker's tree. The cascade flips every parked node
    to RUNNING and re-claims its suspended turn — so a side-effect
    target that a deliberate pause parked AFTER the question pause would
    be resumed by the cascade, before the recorded-set release could
    defer to the operator's park.

    This helper returns the ids the cascade must EXCLUDE. It is the
    same computation the answer path uses
    (``daemon/routers/answer_helper.py``) — fail-open: a probe failure
    yields an empty exclusion list (the pre-fix behaviour).

    NOTE: this mirrors ``daemon/routers/instances.py``'s
    module-level ``_deliberate_pause_exclusions`` helper (which the
    gate-supersession route still uses). The router's copy is NOT
    imported here — a service must not depend on a router — so the two
    wrappers stay in sync by both wrapping the canonical
    ``daemon.services.question_pause_set.deliberate_pause_exclusions``.
    """
    try:
        from daemon.services.question_pause_set import (
            deliberate_pause_exclusions,
        )

        return await deliberate_pause_exclusions(
            manager, instance_id, pack_id
        )
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            f"deliberate-pause exclusion probe failed for "
            f"{instance_id[:8]}...: {type(exc).__name__}: {exc}"
        )
        return []


async def dismiss_question_pack(
    manager: Any,
    instance_id: str,
    live_hub: Any = None,
) -> dict:
    """Dismiss a pending question pack without answering; resume the cascade.

    The instance must have a question pack (set by the ``question`` tool
    before the graph paused). Clears the pack, drops the pause-requested
    flag, discards any deferred-pause marker, emits a ``question_pack``
    SSE event with ``status="dismissed"``, then mirrors the PAUSED-branch
    resume fan-out from ``answer_questions``: cascade-resumes target +
    all paused children, and for each resumed instance calls
    ``resume_processing_job`` — the target receives a HumanMessage
    indicating dismissal; children resume silently from their checkpoint.

    Args:
        manager: The ``InstanceManager`` facade.
        instance_id: The instance whose pending pack is dismissed.
        live_hub: The LiveEventHub used for the ``question_pack`` SSE
            emit, or ``None`` to skip the emit (tests / no hub).

    Returns:
        The success dict — ``{"status", "instance_id", "resume_info"}``.

    Raises:
        DismissWritePaused: The daemon is write-paused (migration).
        DismissInstanceNotFound: The instance is unknown to the manager.
        DismissNoPendingQuestion: No question pack exists.
        DismissPackNotPending: The pack is not in the ``pending`` state.
        DismissCascadeFailed: The resume cascade raised.
    """
    if manager.is_write_paused:
        raise DismissWritePaused("Writes are paused for database migration")

    # 1. Validate instance exists — uniform 404 contract.
    try:
        await manager.get_instance(instance_id)
    except KeyError:
        raise DismissInstanceNotFound(
            f"Instance not found: {instance_id}",
        )

    # 2. Capture the pack BEFORE clearing so the SSE payload can carry
    #    the dismissed question snapshot. Same 404 contract as
    #    answer_questions when no pack exists.
    from daemon.services.question_manager import pack_to_dict

    pack = manager._question_manager.get_question_pack(instance_id)
    if pack is None:
        raise DismissNoPendingQuestion(
            f"No question pack for instance {instance_id!r}. "
            "The user can only dismiss a question that was "
            "actually asked."
        )

    # Reject if already answered/dismissed — dismissing a non-pending
    # pack would emit a misleading ``status="dismissed"`` SSE event and
    # could race the original answer/cascade path. 409 Conflict signals
    # the request is valid in shape but the resource is in the wrong state.
    if pack.status != "pending":
        raise DismissPackNotPending(
            f"Question pack for instance {instance_id!r} is no "
            f"longer pending (status: {pack.status}). Only "
            "pending packs can be dismissed."
        )

    # 3. Unwind question state — mirror _cleanup_instance_state
    #    (manager.py:2238-2245) minus the lifecycle-only entries
    #    (graph_task / pending_injections / request_registry).
    #    The instance is being resumed, NOT terminated.
    manager._question_manager.clear_question_pack(instance_id)
    # M1 (fix pass, council-verified): ALSO clear the DURABLE metadata
    # shadow — same rationale as the gate-supersession site above.
    # Without this, a late answer after dismiss rehydrates the pack
    # from ``instance_metadata`` (answer_helper §3) and injects into
    # the running dismissed-past agent; a daemon restart's boot
    # hydration would resurrect it the same way.
    from daemon.services.midflight_qa import clear_question_pack_metadata

    clear_question_pack_metadata(manager, instance_id)
    manager.clear_question_pause_requested(instance_id)
    # C2 fix — drop the deferred-pause marker. Without this discard the
    # post-graph completion path in instance_messaging would still fire
    # pause_instance_cascade for this instance, racing the resume below
    # and producing the exact torn PAUSED→RUNNING state we just unwound.
    manager._deferred_question_pause.discard(instance_id)

    # 4. Emit SSE: question_pack with status="dismissed". Best-effort —
    #    the resume cascade must proceed even if SSE fails. Reuse the
    #    frozen pack_to_dict schema so the frontend's existing parser
    #    handles the event without code changes.
    if live_hub is not None:
        try:
            dismissed_payload = pack_to_dict(pack)
            dismissed_payload["status"] = "dismissed"
            await live_hub.stream_question_pack(instance_id, dismissed_payload)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"question_pack SSE emission failed for dismiss on "
                f"instance {instance_id}: {e}"
            )

    # 5. Resolve the dismissal handle FIRST (while the task is still
    #    PAUSED), THEN cascade-resume. Same ordering invariant as
    #    ``answer_questions``: ``resume_processing_job`` calls
    #    ``find_suspended_turn_for_answer`` which requires
    #    ``Task.status == 'paused'``. ``resume_instance_cascade`` would
    #    flip that to PENDING first, leaving the dismissal message unable
    #    to route. ``_schedule_explicit_handle_resume`` only injects and
    #    schedules a background task — the cascade below does not cancel
    #    it.
    dismiss_msg = "[User dismissed the question without answering.]"
    try:
        job_result = await manager.resume_processing_job(
            instance_id,
            message=dismiss_msg,
            silent=False,
        )
    except Exception as e:
        logger.warning(
            f"dismiss_question: resume_processing_job failed for "
            f"{instance_id[:8]}...: {e}"
        )
        job_result = {"status": "error", "error": str(e)}
    if job_result is None:
        logger.warning(
            f"dismiss_question: resume_processing_job returned None "
            f"for {instance_id[:8]}... — no suspended turn found"
        )
        job_result = {"status": "no_active_job"}

    # R2 (fix/question-pause-resume-asymmetry) — a dismissal is a
    # question-lifecycle TERMINATION, so it must release the recorded
    # side-effect set exactly like an answer does. The recorded set is
    # the authoritative list of instances the question pause parked.
    #
    # The release runs in a ``finally`` around the cascade: the pack is
    # already consumed at this point, so a raising cascade must not skip
    # the release (a skipped release after a consumed pack IS the
    # stranded state).
    # C2/rider — the DELIBERATE-pause override. The dismiss path's own
    # comment says it must "release exactly like an answer" — so it must
    # apply the SAME exclusion as the answer path: the cascade must not
    # flip (and re-claim the suspended turn of) a side-effect target that
    # a LATER deliberate pause parked. The exclusions are computed BEFORE
    # the cascade call, exactly as in ``answer_helper.py``.
    dismiss_exclude_ids = await _deliberate_pause_exclusions(
        manager, instance_id, getattr(pack, "id", None)
    )

    try:
        try:
            from daemon.services.question_pause_set import (
                resume_instance_cascade_with_exclusions,
            )

            resume_result = await resume_instance_cascade_with_exclusions(
                manager, instance_id, dismiss_exclude_ids
            )
        except Exception as e:
            raise DismissCascadeFailed(
                f"Failed to resume instance: {e}",
            )

        target_id = resume_result.get("target_id", instance_id)

        # Children resume silently from their checkpoint — they were paused
        # as a side effect of the parent's question. The target's resume was
        # already scheduled above; here we only handle non-target children.
        resume_results: dict = {instance_id: job_result}
        for resumed_id in resume_result["resumed_ids"]:
            if resumed_id == target_id:
                continue  # already handled above
            try:
                child_result = await manager.resume_processing_job(
                    resumed_id,
                    message="resume",
                    silent=True,
                )
            except Exception as e:
                logger.warning(
                    f"Failed to resume processing for "
                    f"{resumed_id[:8]}...: {e}"
                )
                child_result = {"status": "error", "error": str(e)}
            if child_result is None:
                logger.debug(
                    f"No active PROCESSING job for instance "
                    f"{resumed_id[:8]}... (was IDLE/WAITING_CHILDREN)"
                )
                child_result = {"status": "no_active_job"}
            resume_results[resumed_id] = child_result

        # W1: Surface a degraded status when the dismissal couldn't be routed.
        # The cascade still ran (children unblocked), but the target's
        # dismissal message was lost — return a distinct status so the
        # caller knows.
        dismiss_status = (
            "dismissed"
            if job_result.get("status") != "no_active_job"
            else "no_active_job"
        )
    finally:
        try:
            from daemon.services.question_pause_set import (
                release_question_paused_set,
            )

            await release_question_paused_set(
                manager,
                instance_id,
                getattr(pack, "id", None),
                reason="dismiss",
            )
        except Exception as unpause_exc:  # noqa: BLE001 — fail-open
            logger.warning(
                f"dismiss_question: recorded-set release failed for "
                f"{instance_id[:8]}...: "
                f"{type(unpause_exc).__name__}: {unpause_exc}"
            )

    return {
        "status": dismiss_status,
        "instance_id": instance_id,
        "resume_info": {
            "resumed": True,
            "resumed_ids": resume_result["resumed_ids"],
            "skipped_ids": resume_result["skipped_ids"],
            "target_id": target_id,
            "resume_results": resume_results,
        },
    }
