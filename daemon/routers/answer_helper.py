"""Shared question-answer helper — single funnel for BOTH answer surfaces.

Mid-flight QA channel (2026-09-21, ``feature/midflight-qa-channel``,
design §5.3). Extracted from the former ~250-line body of
``POST /api/instances/{instance_id}/answer``
(``daemon/routers/instances.py``) so the NEW job-addressed route
(``POST /api/jobs/{work_id}/answer``) and the existing instance-addressed
route share one implementation. ~90% of the original body is shared
logic; the only difference between the two surfaces is the lookup
direction (work_id → instance_id via ``WorkResolver`` happens in the
jobs route BEFORE delegating here).

Guard order (MINOR-14 — binding): the terminal pre-check (404
no-asker / 410 terminal / three-way discriminator) runs BEFORE the
CAS + SSE + events; the CAS ``set_answers → (pack, transitioned)``
arbitrates exactly-once; the loser short-circuits to
``200 already_delivered`` and skips SSE, events, resume, and the
Defect-3 fallback (OQ-3).

Error codes (both surfaces inherit):

==========================  =====  =======================================
Code                       HTTP   Meaning
==========================  =====  =======================================
INSTANCE_NOT_FOUND         404    Instance unknown to the manager. (The
                                   jobs surface pre-empts this with
                                   ``JOB_NOT_FOUND`` at work_id resolve
                                   time, before delegating here.)
NO_PENDING_QUESTION        404    Instance exists, no pack pending. Was
                                   mislabeled ``INSTANCE_NOT_FOUND``.
QUESTION_PACK_LOST         410    Durable handle but neither RAM pack nor
                                   ``instance_metadata`` payload survive
                                   (post-restart loss).
QUESTION_PACK_MISMATCH     400    Body ``question_pack_id`` ≠ current pack
                                   id (stale-answers hijack guard, T1″).
ANSWER_TARGET_TERMINAL     410    Asker is COMPLETED/TERMINATED — answer
                                   rejected (leader decision 1; replaces
                                   today's silent-revive, a latent
                                   data-loss hazard).
ALREADY_DELIVERED          200    CAS lost — no-op with
                                   ``resume_route:"already_delivered"``.
WRITE_PAUSED               503    Daemon migration mode.
==========================  =====  =======================================
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from daemon.models.common import ErrorCodes, ErrorResponse
from daemon.services.question_manager import QuestionPack, pack_to_dict

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)


class AnswerRequest(BaseModel):
    """Request body for the answer surfaces (mid-flight QA channel).

    ONE shared schema for both ``POST /api/instances/{id}/answer``
    (FastAPI-validated) and ``POST /api/jobs/{work_id}/answer``
    (lenient ``model_validate`` on a raw ``body: dict`` — 400 on
    malformed, never a 422 flip).

    Carries the user's answers to a pending question pack. The shape
    of ``answers`` is intentionally flexible — callers may key by
    question id (preferred) or by question text (for ad-hoc clients
    that didn't capture the auto-generated ids).

    Attributes:
        answers: User-supplied answer dict. Shape is unconstrained
            (any JSON-serializable dict); the manager stores it
            verbatim and the resume-message formatter iterates it.
        question_pack_id: Optional pack id for the T1″ stale-answers
            correlation guard (mid-flight QA channel, 2026-09-21).
            When present and ≠ the current pending pack's id the
            request is rejected with ``400 QUESTION_PACK_MISMATCH``.
            Absent = today's lenient behavior.
        resume_message: Optional extra text appended to the Q↔A
            delivery message (the job-addressed surface accepts it;
            kept here so both bodies share one schema).
    """

    answers: dict = Field(
        default_factory=dict,
        description=(
            "User-supplied answers. Shape is flexible: prefer keying "
            "by question id (the field returned in the pending SSE "
            "event) — text-keyed fallbacks are also accepted."
        ),
    )
    question_pack_id: str | None = Field(
        default=None,
        description=(
            "Optional question pack id (from the QUESTION_REQUESTED "
            "payload) — when present, must match the current pending "
            "pack or the answer is rejected 400 QUESTION_PACK_MISMATCH."
        ),
    )
    resume_message: str | None = Field(
        default=None,
        description=(
            "Optional extra message text appended to the delivered "
            "Q↔A HumanMessage (max 2000 chars)."
        ),
    )


def _http(status: int, code: ErrorCodes, message: str, details: dict | None = None):
    return HTTPException(
        status_code=status,
        detail=ErrorResponse(code=code, message=message, details=details).model_dump(),
    )


def _format_answer_message(pack: QuestionPack) -> str:
    """Format the Q↔A HumanMessage (F7 compaction-safe echo, unchanged)."""
    answer_lines = ["Here are the user's answers to your questions:", ""]
    for i, q in enumerate(pack.questions):
        answer = (
            pack.answers.get(q.id)
            if q.id in pack.answers
            else pack.answers.get(q.text, "(no answer)")
        )
        answer_lines.append(f"**Q{i + 1}:** {q.text}")
        answer_lines.append(f"**A{i + 1}:** {answer}")
        answer_lines.append("")
    return "\n".join(answer_lines)


async def _load_asker(
    manager: "InstanceManager",
    instance_id: str,
) -> bool:
    """Banners 0-2: write-pause guard, instance existence, T3 terminal
    pre-check (MINOR-14: BEFORE CAS + SSE + events).

    Returns ``reviving_error_target`` — True when the asker is
    ERROR/FAILED and the answer will land via the Defect-3
    fresh-message branch.
    """
    from daemon.services.midflight_qa import ANSWER_TERMINAL_STATUSES

    # ── 0. Write-pause guard (503 migration posture, both surfaces) ──
    if manager.is_write_paused:
        raise HTTPException(
            status_code=503,
            detail="Writes are paused for database migration",
        )

    # ── 1. Instance existence (uniform 404 contract) ─────────────────
    instance_row = None
    try:
        # NIT-8 (fix pass): the repository read is a blocking sync DB
        # call — wrap in asyncio.to_thread (sibling precedent:
        # HeartbeatEmitStuckProcessor / jobs_management answer route).
        instance_row = await asyncio.to_thread(
            manager._instance_repository.get, instance_id
        )
    except KeyError:
        # Defensive backstop: RAM-shaped repositories may signal a
        # missing row by KeyError instead of returning None — an
        # expected miss, same 404 outcome, no warn-spam.
        instance_row = None
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"answer_questions_via_instance: instance read failed for "
            f"{instance_id[:8]}...: {e}"
        )
    if instance_row is None:
        raise _http(
            404,
            ErrorCodes.INSTANCE_NOT_FOUND,
            f"Instance {instance_id!r} not found.",
        )
    asker_status = getattr(instance_row, "status", None)

    # ── 2. T3 terminal pre-check (MINOR-14: BEFORE CAS + SSE + events).
    reviving_error_target = False
    if asker_status in ANSWER_TERMINAL_STATUSES:
        # COMPLETED/TERMINATED → 410 (leader decision 1). Tightens
        # today's silent-revive (`enqueue_message` to a terminal
        # instance restarts it from scratch with no checkpoint — a
        # latent data-loss hazard). FE impact: the wizard's existing
        # error handler surfaces the message; no FE code relies on
        # silent-revive (MAJOR-3 grep finding).
        raise _http(
            410,
            ErrorCodes.ANSWER_TARGET_TERMINAL,
            (
                f"Asker instance {instance_id[:8]}... is "
                f"{asker_status!r} — the answer cannot be delivered. "
                f"The instance reached a terminal state while the "
                f"question was pending."
            ),
            details={"instance_id": instance_id, "status": asker_status},
        )
    if asker_status in ("error", "failed"):
        # ERROR/FAILED → genuine recovery affordance: the handle is
        # gone anyway (failed/errored tasks clear suspension_reason),
        # so the answer lands via the Defect-3 fresh-message branch.
        # Mirrors user-API revive semantics — NO revive-budget
        # consumption (the answer path is user-origin).
        reviving_error_target = True
        logger.info(
            f"answer_questions_via_instance: asker {instance_id[:8]}... "
            f"in {asker_status!r} — reviving to deliver the answer "
            f"(resume_route=revived_error_target)"
        )
    return reviving_error_target


async def _resolve_pack_or_raise(
    manager: "InstanceManager",
    instance_id: str,
    question_pack_id: str | None,
) -> QuestionPack:
    """Banners 3-4: pack resolution + on-the-fly rehydration (§5.4),
    then the T1″ pack correlation guard (pre-CAS;
    strict-check-when-present)."""
    from daemon.services.midflight_qa import QUESTION_PACK_PAYLOAD_METADATA_KEY

    qm = manager._question_manager
    pack = qm.get_question_pack(instance_id)

    if pack is None:
        # RAM pack missing (daemon restart) — attempt one-shot
        # rehydration from the durable ``instance_metadata`` shadow.
        try:
            payload = manager._instance_repository.get_metadata_value(
                instance_id, QUESTION_PACK_PAYLOAD_METADATA_KEY
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"answer_questions_via_instance: pack payload read "
                f"failed for {instance_id[:8]}...: {e} — treating as "
                f"no durable shadow"
            )
            payload = None
        if isinstance(payload, dict) and payload.get("status") == "pending":
            restored = qm.rehydrate_from_payloads({instance_id: payload})
            if restored:
                pack = qm.get_question_pack(instance_id)
        elif isinstance(payload, dict):
            # A durable shadow EXISTS but is not pending (answered /
            # dismissed before the RAM store was lost) — the instance
            # has no pack in status='pending'.
            raise _http(
                404,
                ErrorCodes.NO_PENDING_QUESTION,
                (
                    f"No pending question pack for instance "
                    f"{instance_id[:8]}... (durable shadow status="
                    f"{payload.get('status')!r})."
                ),
                details={"instance_id": instance_id},
            )
        if pack is None:
            raise _http(
                410,
                ErrorCodes.QUESTION_PACK_LOST,
                (
                    f"No question pack for instance {instance_id[:8]}... "
                    f"in memory or in instance_metadata — the pack was "
                    f"lost (daemon restart without a durable shadow)."
                ),
                details={"instance_id": instance_id},
            )

    # NOTE: a RAM pack with status='answered' deliberately FALLS
    # THROUGH to the CAS below — the duplicate-answer contract (OQ-3 /
    # §8.5) is ``200 resume_route:"already_delivered"``, not a 404. The
    # CAS arbitrates; the loser short-circuits.

    # ── 4. T1″ pack correlation (pre-CAS; strict-check-when-present) ─
    if question_pack_id is not None and question_pack_id != pack.id:
        raise _http(
            400,
            ErrorCodes.QUESTION_PACK_MISMATCH,
            (
                f"question_pack_id {question_pack_id!r} does not match "
                f"the current pending pack {pack.id!r} — stale answers "
                f"for a superseded question (the asker re-asked)."
            ),
            details={
                "instance_id": instance_id,
                "expected_pack_id": pack.id,
                "got_pack_id": question_pack_id,
            },
        )
    return pack


async def _emit_answer_sse(live_hub: Any, instance_id: str, pack: QuestionPack) -> None:
    """Banner 6: SSE (best-effort) — answered pack + answer banner."""
    if live_hub is not None:
        try:
            await live_hub.stream_question_pack(instance_id, pack_to_dict(pack))
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"question_pack SSE emission failed for answer on "
                f"instance {instance_id[:8]}...: {e}"
            )
        try:
            await live_hub.stream_answer_received(
                instance_id,
                {
                    "instance_id": instance_id,
                    "question_pack_id": pack.id,
                    "resume_route": "pending",
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"answer_received SSE emission failed for "
                f"instance {instance_id[:8]}...: {e}"
            )


async def _emit_answer_events(
    manager: "InstanceManager",
    instance_id: str,
    pack: QuestionPack,
    answers: dict,
    delivery_ms: int,
    resume_route: str,
) -> None:
    """Banner 7: QUESTION_ANSWERED event + watcher fan-out (best-effort,
    §8.6)."""
    from daemon.repositories.event.models import EventKind
    from daemon.services.midflight_qa import enumerate_live_work_ids
    from daemon.services.work_notifier import notify_work_watchers

    work_ids = enumerate_live_work_ids(manager, instance_id)
    event_bus = getattr(manager, "_event_bus", None)

    try:
        if event_bus is not None:
            await event_bus.create_event(
                instance_id=instance_id,
                kind=EventKind.QUESTION_ANSWERED,
                data={
                    "instance_id": instance_id,
                    # Legacy wire key: "job_id" carries the asker's
                    # work ids (a rename would break the wire contract).
                    "job_id": work_ids,
                    "question_pack_id": pack.id,
                    "answers": dict(answers) if isinstance(answers, dict) else {},
                    "resume_route": resume_route,
                    "delivery_ms": delivery_ms,
                },
            )
    except Exception as e:  # noqa: BLE001 — §8.6
        logger.warning(
            f"QUESTION_ANSWERED EventBus emission failed for "
            f"instance {instance_id[:8]}...: {e}"
        )

    for work_id in work_ids:
        try:
            await notify_work_watchers(
                work_id=work_id,
                status="answer_received",
                instance_manager=manager,
                work_resolver=getattr(manager, "_work_resolver", None),
                watcher_repo=getattr(manager, "_watcher_repo", None),
                result_summary=pack_to_dict(pack),
            )
        except Exception as e:  # noqa: BLE001 — §8.6
            logger.warning(
                f"answer_received fan-out failed for work_id="
                f"{work_id[:8] if work_id else '<none>'}...: {e}"
            )


async def _fallback_enqueue_or_raise(
    manager: "InstanceManager",
    instance_id: str,
    answer_msg: str,
    reviving_error_target: bool,
) -> tuple[dict, str]:
    """Defect-3 fallback (answer-gate resume chain, 2026-09-10), banner
    8's ``job_result is None`` branch: NEVER 200-mask an undeliverable
    answer — enqueue the Q↔A payload as a fresh user message. For a
    revived ERROR/FAILED target this is the expected route (the handle
    is gone).

    Returns ``(job_result, resume_route)``; raises 410 on a
    late-terminal asker, 500 when the enqueue itself fails.
    """
    from daemon.services.midflight_qa import ANSWER_TERMINAL_STATUSES

    logger.warning(
        f"answer_questions_via_instance: resume_processing_job returned "
        f"None for {instance_id[:8]}... — Defect-3 fallback: "
        f"enqueueing answer as fresh user message"
        + (" (revived_error_target)" if reviving_error_target else "")
    )
    # M2 (fix pass, council-verified) — terminal TOCTOU guard: the
    # asker's status was pre-checked at step 2, but the fallback is
    # reached SECONDS later (resume attempt + SSE + event fan-out).
    # In that window the asker can reach a terminal state — most
    # notably the wedge-guard escalation, which terminates the chain
    # at t≈3600s, exactly when late answers tend to land.
    # ``enqueue_message``'s source-agnostic revive-on-terminal
    # (instance_messaging.py:1898-1925) would then silently revive
    # a TERMINATED asker from scratch (no checkpoint — the latent
    # data-loss hazard leader decision 1 closed at the pre-check).
    # Re-read the status; refuse with 410 instead of enqueueing.
    # ``reviving_error_target`` is the sanctioned revive path
    # (ERROR/FAILED pre-checked at step 2) and stays exempt.
    late_status: str | None = None
    try:
        late_row = await asyncio.to_thread(
            manager._instance_repository.get, instance_id
        )
        late_status = getattr(late_row, "status", None) if late_row else None
    except Exception as e:  # noqa: BLE001 — fail-open to today's behavior
        logger.warning(
            f"answer_questions_via_instance: fallback terminal "
            f"re-read failed for {instance_id[:8]}...: {e}"
        )
    if (
        late_status in ANSWER_TERMINAL_STATUSES
        and not reviving_error_target
    ):
        raise _http(
            410,
            ErrorCodes.ANSWER_TARGET_TERMINAL,
            (
                f"Asker instance {instance_id[:8]}... became "
                f"{late_status!r} while the answer was being "
                f"delivered (terminal TOCTOU, Defect-3 fallback) — "
                f"the answer was stored but cannot be injected; "
                f"refusing to enqueue onto (or revive) a terminal "
                f"asker."
            ),
            details={
                "instance_id": instance_id,
                "status": late_status,
                "resume_route": "defect3_refused_terminal",
            },
        )
    try:
        fallback_result = await manager.enqueue_message(
            instance_id=instance_id,
            message=answer_msg,
            source="api_answer_fallback",
        )
        job_result = {
            "status": "enqueued_as_fresh_message",
            "message_id": fallback_result.message_id,
            "job_id": fallback_result.job_id,
            "instance_id": instance_id,
            "route": "api_answer_fallback",
        }
        resume_route = "revived_error_target" if reviving_error_target else (
            "enqueue_as_fresh_message"
        )
        return job_result, resume_route
    except Exception as enqueue_err:  # noqa: BLE001
        logger.error(
            f"answer_questions_via_instance: Defect-3 fallback enqueue "
            f"failed for {instance_id[:8]}...: {enqueue_err}",
            exc_info=True,
        )
        raise _http(
            500,
            ErrorCodes.INTERNAL_ERROR,
            (
                f"Failed to deliver answer: no awaiting_answer handle "
                f"and fallback enqueue also failed: {enqueue_err}"
            ),
        )


async def _resume_target_and_tree(
    manager: "InstanceManager",
    instance_id: str,
    job_result: dict,
    pack: QuestionPack,
    resume_route: str,
) -> dict:
    """Banner 9: cascade-resume the tree (transitions PAUSED→RUNNING /
    task PAUSED→PENDING; the scheduled background resume survives — the
    cascade does not touch _graph_tasks) and assemble the final
    response."""
    try:
        resume_result = await manager.resume_instance_cascade(instance_id)
    except Exception as e:  # noqa: BLE001
        raise _http(
            500,
            ErrorCodes.INTERNAL_ERROR,
            f"Failed to resume instance: {e}",
        )

    target_id = resume_result.get("target_id", instance_id)

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
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to resume processing for {resumed_id[:8]}...: {e}")
            child_result = {"status": "error", "error": str(e)}
        if child_result is None:
            logger.debug(
                f"No active PROCESSING job for instance {resumed_id[:8]}... "
                f"(was IDLE/WAITING_CHILDREN)"
            )
            child_result = {"status": "no_active_job"}
        resume_results[resumed_id] = child_result

    # W1: surface a degraded-but-delivered status on the fallback path.
    if job_result.get("status") == "enqueued_as_fresh_message":
        answer_status = "answer_fallback_enqueued"
    elif job_result.get("status") != "no_active_job":
        answer_status = "answered"
    else:
        answer_status = "no_active_job"

    return {
        "status": answer_status,
        "instance_id": instance_id,
        "question_pack": pack_to_dict(pack),
        "resume_route": resume_route,
        "resume_info": {
            "resumed": True,
            "resumed_ids": resume_result["resumed_ids"],
            "skipped_ids": resume_result["skipped_ids"],
            "target_id": target_id,
            "resume_results": resume_results,
        },
    }


async def answer_questions_via_instance(
    manager: "InstanceManager",
    instance_id: str,
    answers: dict,
    question_pack_id: str | None,
    live_hub: Any,
    resume_message: str | None = None,
) -> dict:
    """Store answers, emit on the push lanes, resume the asker cascade.

    See the module docstring for the guard table and ordering contract.
    Raises :class:`HTTPException` on every guarded failure; returns the
    success dict (superset of the legacy shape, plus ``resume_route``).

    Args:
        manager: The InstanceManager.
        instance_id: The ASKER instance id.
        answers: User-supplied answer dict (flexible shape).
        question_pack_id: Optional pack id echoed by the caller for the
            T1″ correlation guard. ``None`` = today's lenient behavior.
        live_hub: The LiveEventHub (or ``None`` in tests).
        resume_message: Optional extra message text appended by the
            caller (the jobs surface accepts ``resume_message``).
    """
    from daemon.services.midflight_qa import clear_question_pack_metadata

    started = time.monotonic()

    # ── 0-2. Write-pause guard + instance existence + T3 terminal
    #    pre-check (MINOR-14: BEFORE CAS + SSE + events) ──────────────
    reviving_error_target = await _load_asker(manager, instance_id)

    # ── 3. Pack resolution + on-the-fly rehydration (§5.4) ───────────
    # ── 4. T1″ pack correlation (pre-CAS; strict-check-when-present) ─
    pack = await _resolve_pack_or_raise(manager, instance_id, question_pack_id)

    # ── 5. T1 CAS — exactly-once pending → answered ──────────────────
    # MINOR-6 (fix pass) — accepted benign race, pre-check → CAS window:
    # the asker's status can CHANGE between the step-2 terminal
    # pre-check above and this CAS (e.g. it completes, crashes, or the
    # wedge-guard escalates). That is ACCEPTED behavior: the CAS itself
    # is the arbitration point — it flips the pack to ``answered`` (the
    # answer is DURABLE in the RAM store, and the metadata shadow is
    # cleared right below), the resume paths are idempotent, and the
    # Defect-3 fallback re-checks terminality before enqueueing (M2
    # below) so a late-terminated asker is refused with 410 instead of
    # being silently revived. No TOCTOU guard is added here.
    qm = manager._question_manager
    pack, transitioned = qm.set_answers(instance_id, answers)

    # R3: the durable metadata shadow is cleared at ANSWER CONSUMPTION
    # on BOTH branches — never at pack-create time (a create-site clear
    # leaves a stale-rehydration window).
    clear_question_pack_metadata(manager, instance_id)

    if not transitioned:
        # T1′ CAS loser: duplicate answer. No-op — NO SSE, NO events,
        # NO resume, NO Defect-3 (OQ-3).
        return {
            "status": "already_delivered",
            "instance_id": instance_id,
            "question_pack": pack_to_dict(pack),
            "resume_route": "already_delivered",
        }

    delivery_ms = int((time.monotonic() - started) * 1000)

    # ── 6. SSE (best-effort) — answered pack + answer banner ─────────
    await _emit_answer_sse(live_hub, instance_id, pack)

    # ── 7. QUESTION_ANSWERED event + watcher fan-out ─────────────────
    answer_msg = _format_answer_message(pack)
    if resume_message:
        answer_msg = f"{answer_msg}\n{resume_message}"

    resume_route = "answer_gate_existing_turn"
    await _emit_answer_events(
        manager, instance_id, pack, answers, delivery_ms, resume_route
    )

    # ── 8. Resume: handle-first, then cascade (unchanged ordering) ───
    try:
        job_result = await manager.resume_processing_job(
            instance_id,
            message=answer_msg,
            silent=False,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"answer_questions_via_instance: resume_processing_job failed "
            f"for {instance_id[:8]}...: {e}"
        )
        job_result = {"status": "error", "error": str(e)}

    if reviving_error_target:
        resume_route = "revived_error_target"

    if job_result is None:
        # Defect-3 fallback (answer-gate resume chain, 2026-09-10):
        # NEVER 200-mask an undeliverable answer — enqueue the Q↔A
        # payload as a fresh user message. For a revived ERROR/FAILED
        # target this is the expected route (the handle is gone).
        job_result, resume_route = await _fallback_enqueue_or_raise(
            manager, instance_id, answer_msg, reviving_error_target
        )

    # ── 9. Cascade-resume the tree (transitions PAUSED→RUNNING /
    #        task PAUSED→PENDING; the scheduled background resume
    #        survives — the cascade does not touch _graph_tasks) ──────
    return await _resume_target_and_tree(
        manager, instance_id, job_result, pack, resume_route
    )
