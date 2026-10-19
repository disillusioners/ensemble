"""Job-scoped answer-target resolution (2026-10-19).

Shared resolver for the TWO job-addressed answer surfaces:

* ``POST /api/jobs/{work_id}/answer`` (``daemon/routers/jobs_management.py``)
* the ``job_answer`` agent tool (``daemon/tools/job_queue.py``)

Both surfaces used to route the answer at ``WorkRecord.instance_id`` —
the work unit's OWNING instance. For a mission-scoped ``work_id`` that
is the mission ROOT, even when the question was actually asked by a
CHILD instance. The answer then landed on an instance that never held
the pack, and the shared helper raised a 410 ``QUESTION_PACK_LOST``
whose message blamed a "daemon restart without a durable shadow" — a
claim that is FALSE in the wrong-instance class (the pack was alive and
durable on the child).

This module resolves the ASKING instance inside the mission subtree.
It is READ-ONLY (no writes, no migrations, no new tables) and
watcher-independent (it only uses the work record + the instance/task
repositories).

Resolution order (one shared helper — both surfaces call it):

1. **Fast path** — the work's own instance already holds the PENDING
   pack the caller means (a RAM pack, or a durable ``question_pack_id``
   shadow matching the supplied pack id). A RAM pack that is NOT
   pending (e.g. an ANSWERED pack left behind on the mission root —
   RAM packs survive answer consumption) never short-circuits the
   resolution: the answer must reach the instance that still holds a
   live question. Used unchanged otherwise: zero extra queries, and the
   "asker IS the mission root" case keeps today's behaviour.
2. **Pack id** (strongest key) — the subtree instance whose durable
   ``question_pack_id`` shadow (or pending RAM pack) matches the
   supplied pack id.
3. **Authoritative awaiting-answer handle** — the subtree instance
   whose task is ``status='paused' AND suspension_reason=
   'awaiting_answer' AND resume_target_turn_id IS NOT NULL``
   (reused from ``TaskRepository.find_suspended_turn_for_answer``).
   Exactly one candidate wins. Several candidates WITHOUT a pack id is
   an ambiguity we refuse to resolve by guesswork — the caller is told
   to supply ``question_pack_id``.
4. **No candidate** — no pack is invented; the caller raises the
   accurate "no pack-holding asker in this mission subtree" error
   (404 ``NO_PENDING_QUESTION``) instead of the old false 410.

The INSTANCE-addressed surface (``POST /api/instances/{id}/answer``)
is deliberately NOT routed through here — the caller names the
instance explicitly, so there is nothing to resolve.

Module hygiene: the resolver never raises for "not found"; it returns
an :class:`AnswerTarget` whose ``error`` field says what happened, and
:func:`raise_unresolved_target` turns that into the single typed
``HTTPException`` both surfaces share.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn

from daemon.models.common import ErrorCodes
from daemon.services.midflight_qa import (
    QUESTION_PACK_ID_METADATA_KEY,
    QUESTION_PACK_PAYLOAD_METADATA_KEY,
)

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)


# ── Tunables ────────────────────────────────────────────────────────────

# Hard cap on the subtree scan. Mission trees are small (a leader plus
# a handful of children); the cap bounds a pathological fan-out so the
# resolution can never degrade into an unbounded table walk.
MAX_SUBTREE_SCAN = 64


# ── Resolution routes (diagnostic — surfaced in the success envelope) ──

ROUTE_DIRECT_RAM_PACK = "direct_ram_pack"
ROUTE_DIRECT_METADATA_SHADOW = "direct_metadata_shadow"
ROUTE_SUBTREE_PACK_ID = "subtree_pack_id"
ROUTE_SUBTREE_ANSWER_HANDLE = "subtree_answer_handle"


# ── Error kinds ─────────────────────────────────────────────────────────

ERROR_AMBIGUOUS = "ambiguous"
ERROR_NOT_FOUND = "not_found"


@dataclass(frozen=True)
class AnswerTarget:
    """Resolution outcome for a job-scoped answer request.

    Attributes:
        asker_instance_id: The instance that HOLDS the question pack —
            the answer must be delivered here. ``None`` when no asker
            could be identified (see ``error``).
        route: How the asker was identified (one of the ``ROUTE_*``
            constants). ``""`` when unresolved.
        work_instance_id: The mission-scoped instance the work record
            resolved to (the old, wrong target in the defect class).
        mission_root_id: The mission root of ``work_instance_id``
            (permanent lineage).
        subtree_scanned: True when the mission subtree was searched.
        candidate_ids: The candidate instance ids that were considered
            (populated for the ambiguous case).
        pack_id_searched: The pack id the caller asked for (may be
            ``None``).
        error: ``None`` on success, else ``"ambiguous"`` (several
            candidates, no pack id to disambiguate) or ``"not_found"``
            (nobody in the subtree holds a pack).
    """

    asker_instance_id: str | None
    route: str
    work_instance_id: str
    mission_root_id: str
    subtree_scanned: bool = False
    candidate_ids: tuple[str, ...] = ()
    pack_id_searched: str | None = None
    error: str | None = None

    def detail_fields(self) -> dict[str, Any]:
        """Additive ``details`` payload for the answer error envelope.

        Consumers (and tests) can assert on these fields instead of
        pattern-matching the human-readable prose.
        """
        return {
            "resolved_instance_id": self.asker_instance_id
            or self.work_instance_id,
            "work_instance_id": self.work_instance_id,
            "mission_root_id": self.mission_root_id,
            "question_pack_id": self.pack_id_searched,
            "subtree_scanned": self.subtree_scanned,
            "resolution_route": self.route or None,
            "candidate_ids": list(self.candidate_ids),
        }


# ── Small defensive readers ────────────────────────────────────────────
#
# The repositories are sometimes reached through a partially-mocked
# manager (test doubles, partial bootstrap). A ``MagicMock`` attribute
# would look truthy to a naive ``is not None`` check and could turn
# into a false "this instance holds a pack" / "this instance has an
# answer handle" conclusion. The readers below only accept the real
# shapes (a ``str`` pack id, a task row whose status/reason/target
# actually match the authoritative filter).


def _pack_id_of(pack: Any) -> str | None:
    """Return the pack id of a RAM pack, or ``None``.

    ``None`` for anything that is not a real pack (missing pack, or a
    mock object whose ``id`` is not a string).
    """
    pack_id = getattr(pack, "id", None)
    if isinstance(pack_id, str) and pack_id:
        return pack_id
    return None


def _is_answer_handle(task: Any) -> bool:
    """True when ``task`` is the authoritative awaiting-answer handle.

    Mirrors ``TaskRepository.find_suspended_turn_for_answer``'s filter
    shape (status + suspension reason + resume target) so a mock row
    can never masquerade as a real handle.
    """
    if task is None:
        return False
    status = getattr(task, "status", None)
    if not isinstance(status, str):
        return False
    reason = getattr(task, "suspension_reason", None)
    if not isinstance(reason, str):
        return False
    if reason != "awaiting_answer":
        return False
    if getattr(task, "resume_target_turn_id", None) is None:
        return False
    return status == "paused"


def _read_shadow_id(instance_repo: Any, instance_id: str) -> str | None:
    """Read the durable ``question_pack_id`` shadow for ``instance_id``."""
    if instance_repo is None:
        return None
    try:
        value = instance_repo.get_metadata_value(
            instance_id, QUESTION_PACK_ID_METADATA_KEY
        )
    except Exception as e:  # noqa: BLE001 — best-effort read
        logger.warning(
            "answer_resolver: question_pack_id shadow read failed for %s: %s",
            instance_id[:8],
            e,
        )
        return None
    return value if isinstance(value, str) and value else None


def _read_shadow_payload(instance_repo: Any, instance_id: str) -> Any:
    """Read the durable ``question_pack_payload`` shadow for ``instance_id``."""
    if instance_repo is None:
        return None
    try:
        return instance_repo.get_metadata_value(
            instance_id, QUESTION_PACK_PAYLOAD_METADATA_KEY
        )
    except Exception as e:  # noqa: BLE001 — best-effort read
        logger.warning(
            "answer_resolver: question_pack_payload shadow read failed "
            "for %s: %s",
            instance_id[:8],
            e,
        )
        return None


# ── The resolver ────────────────────────────────────────────────────────


def resolve_answer_target(
    manager: "InstanceManager",
    work_instance_id: str,
    question_pack_id: str | None = None,
) -> AnswerTarget:
    """Resolve the ASKING instance for a job-scoped answer request.

    Args:
        manager: The ``InstanceManager`` (read-only use here).
        work_instance_id: ``WorkRecord.instance_id`` — the mission-scoped
            instance the work unit resolved to (usually the mission
            root).
        question_pack_id: The pack id the caller supplied (the
            ``question_pack_id`` echoed from the ``QUESTION_REQUESTED``
            payload). ``None`` = the caller did not supply one.

    Returns:
        An :class:`AnswerTarget`. On success ``asker_instance_id`` is
        set; otherwise ``error`` is ``"ambiguous"`` or ``"not_found"``
        and ``candidate_ids`` carries the evidence.
    """
    instance_repo = getattr(manager, "_instance_repository", None)
    qm = getattr(manager, "_question_manager", None)

    # ── Mission root (permanent lineage) ──────────────────────────────
    mission_root_id = work_instance_id
    if instance_repo is not None:
        try:
            mission_root_id = (
                instance_repo.get_tree_root_id(work_instance_id)
                or work_instance_id
            )
        except Exception as e:  # noqa: BLE001 — degrade to the work id
            logger.warning(
                "answer_resolver: tree root lookup failed for %s: %s",
                work_instance_id[:8],
                e,
            )

    # ── 1. FAST PATH — the work's own instance already holds the pack ─
    ram_pack = qm.get_question_pack(work_instance_id) if qm is not None else None
    ram_pack_id = _pack_id_of(ram_pack)
    # The fast path accepts ONLY a PENDING pack. A RAM pack SURVIVES
    # answer consumption — only the durable metadata shadow is cleared
    # at consumption — so an ANSWERED pack can sit on the mission root
    # long after its own question was answered. Short-circuiting on it
    # routes the answer at the root: the CAS then LOSES (the pack is
    # already answered) and the caller gets a silent 200
    # ``already_delivered`` while the LIVE question on a CHILD asker is
    # never answered and the child stays paused. A non-pending pack
    # must fall through to the subtree pack-id scan / awaiting-answer
    # handle scan so the resolution lands on the real asker.
    ram_pack_pending = (
        ram_pack_id is not None
        and getattr(ram_pack, "status", None) == "pending"
    )
    if ram_pack_pending and (
        question_pack_id is None or ram_pack_id == question_pack_id
    ):
        # The asker IS the mission-scoped instance (today's behaviour,
        # zero extra queries).
        return AnswerTarget(
            asker_instance_id=work_instance_id,
            route=ROUTE_DIRECT_RAM_PACK,
            work_instance_id=work_instance_id,
            mission_root_id=mission_root_id,
            subtree_scanned=False,
            pack_id_searched=question_pack_id,
        )

    if question_pack_id is not None:
        # The durable shadow of the work's own instance carries the
        # requested pack id → the work's instance IS the asker (the
        # RAM store may simply be empty after a restart — the
        # v0.25.3 durable-shadow rehydrate path picks it up from here).
        if _read_shadow_id(instance_repo, work_instance_id) == question_pack_id:
            return AnswerTarget(
                asker_instance_id=work_instance_id,
                route=ROUTE_DIRECT_METADATA_SHADOW,
                work_instance_id=work_instance_id,
                mission_root_id=mission_root_id,
                subtree_scanned=False,
                pack_id_searched=question_pack_id,
            )
    elif ram_pack_id is None:
        # No pack id supplied AND no RAM pack: keep today's per-instance
        # durable-shadow rehydrate path alive for the mission-scoped
        # instance (a pending shadow means the asker is this instance).
        payload = _read_shadow_payload(instance_repo, work_instance_id)
        if isinstance(payload, dict) and payload.get("status") == "pending":
            return AnswerTarget(
                asker_instance_id=work_instance_id,
                route=ROUTE_DIRECT_METADATA_SHADOW,
                work_instance_id=work_instance_id,
                mission_root_id=mission_root_id,
                subtree_scanned=False,
                pack_id_searched=_read_shadow_id(
                    instance_repo, work_instance_id
                ),
            )

    # ── 2. SUBTREE SCAN ───────────────────────────────────────────────
    subtree: list[str] = []
    if instance_repo is not None:
        try:
            subtree = list(
                instance_repo.get_cascade_tree_ids(mission_root_id) or []
            )
        except Exception as e:  # noqa: BLE001 — degrade to the work id
            logger.warning(
                "answer_resolver: cascade tree enumeration failed for %s: %s",
                mission_root_id[:8],
                e,
            )
    if work_instance_id not in subtree:
        subtree.insert(0, work_instance_id)
    if len(subtree) > MAX_SUBTREE_SCAN:
        logger.warning(
            "answer_resolver: subtree scan capped at %d instances "
            "(mission %s has %d) — resolution may miss a candidate",
            MAX_SUBTREE_SCAN,
            mission_root_id[:8],
            len(subtree),
        )
        subtree = subtree[:MAX_SUBTREE_SCAN]

    # 2a. PACK ID — the strongest key.
    if question_pack_id is not None:
        pack_matches: list[str] = []
        for iid in subtree:
            candidate = qm.get_question_pack(iid) if qm is not None else None
            if _pack_id_of(candidate) == question_pack_id:
                pack_matches.append(iid)
        if not pack_matches:
            # No RAM match → consult the durable shadows.
            for iid in subtree:
                if _read_shadow_id(instance_repo, iid) == question_pack_id:
                    pack_matches.append(iid)
        if len(pack_matches) == 1:
            return AnswerTarget(
                asker_instance_id=pack_matches[0],
                route=ROUTE_SUBTREE_PACK_ID,
                work_instance_id=work_instance_id,
                mission_root_id=mission_root_id,
                subtree_scanned=True,
                pack_id_searched=question_pack_id,
            )
        if len(pack_matches) > 1:
            # A pack id is unique — two holders is a data anomaly. We
            # refuse to guess (the same "never pick arbitrarily" rule
            # the awaiting-answer handle path follows).
            logger.warning(
                "answer_resolver: pack id %s matched %d instances in "
                "mission %s — refusing to guess",
                question_pack_id,
                len(pack_matches),
                mission_root_id[:8],
            )
            return AnswerTarget(
                asker_instance_id=None,
                route="",
                work_instance_id=work_instance_id,
                mission_root_id=mission_root_id,
                subtree_scanned=True,
                candidate_ids=tuple(pack_matches),
                pack_id_searched=question_pack_id,
                error=ERROR_AMBIGUOUS,
            )
        # No pack-id holder → fall through to the handle scan so the
        # per-instance error shape (mismatch / lost / no-pending) is
        # produced by the asker that actually holds a pack.

    # 2b. AUTHORITATIVE AWAITING-ANSWER HANDLE.
    handle_matches: list[str] = []
    task_repo = getattr(manager, "_task_repo", None)
    if task_repo is not None:
        for iid in subtree:
            try:
                handle = task_repo.find_suspended_turn_for_answer(iid)
            except ValueError:
                # Duplicate suspended answer-gate rows for one instance
                # = invariant violation. Treat the instance as a
                # candidate (ambiguity is surfaced, never resolved by
                # recency).
                logger.warning(
                    "answer_resolver: duplicate awaiting-answer handles "
                    "for %s — treating as ambiguous candidate",
                    iid[:8],
                )
                handle_matches.append(iid)
                continue
            except Exception as e:  # noqa: BLE001 — best-effort read
                logger.warning(
                    "answer_resolver: answer-handle lookup failed for "
                    "%s: %s",
                    iid[:8],
                    e,
                )
                continue
            if _is_answer_handle(handle):
                handle_matches.append(iid)

    if len(handle_matches) == 1:
        return AnswerTarget(
            asker_instance_id=handle_matches[0],
            route=ROUTE_SUBTREE_ANSWER_HANDLE,
            work_instance_id=work_instance_id,
            mission_root_id=mission_root_id,
            subtree_scanned=True,
            pack_id_searched=question_pack_id,
        )
    if len(handle_matches) > 1:
        # Several askers are waiting for an answer and the caller gave
        # no pack id to disambiguate. Picking one by recency would
        # answer the WRONG question — refuse instead.
        logger.warning(
            "answer_resolver: %d instances in mission %s hold an "
            "awaiting-answer handle and no question_pack_id was "
            "supplied — refusing to guess",
            len(handle_matches),
            mission_root_id[:8],
        )
        return AnswerTarget(
            asker_instance_id=None,
            route="",
            work_instance_id=work_instance_id,
            mission_root_id=mission_root_id,
            subtree_scanned=True,
            candidate_ids=tuple(handle_matches),
            pack_id_searched=question_pack_id,
            error=ERROR_AMBIGUOUS,
        )

    # ── 3. NO CANDIDATE ───────────────────────────────────────────────
    # Nobody in the mission subtree holds a pack. Do NOT invent one and
    # do NOT claim the pack was lost — the accurate answer is "there is
    # no pack-holding asker in this mission".
    return AnswerTarget(
        asker_instance_id=None,
        route="",
        work_instance_id=work_instance_id,
        mission_root_id=mission_root_id,
        subtree_scanned=True,
        pack_id_searched=question_pack_id,
        error=ERROR_NOT_FOUND,
    )


# ── The one typed error both surfaces share ────────────────────────────


def raise_unresolved_target(target: AnswerTarget) -> NoReturn:
    """Raise the accurate HTTP error for an unresolved job-scoped answer.

    Two shapes, distinguished on purpose (the old code conflated them
    behind a 410 whose message blamed a daemon restart that may never
    have happened):

    * ``ambiguous`` → **400** ``INVALID_REQUEST``. The request is
      under-specified: several instances hold a question and the caller
      did not supply ``question_pack_id``; OR the supplied
      ``question_pack_id`` matched SEVERAL instances (a data anomaly —
      a pack id is unique); OR the supplied pack id matched nobody while
      several instances hold an awaiting-answer handle. In every shape
      the resolver refuses to guess. ``INVALID_REQUEST`` is the
      repo's existing generic 400 token (the answer route already uses
      it for a malformed body) — reusing it keeps the wire vocabulary
      closed and avoids a new ``ErrorCodes`` member that no consumer
      knows how to render. ``QUESTION_PACK_MISMATCH`` would be wrong:
      that token means "the pack id you gave is NOT the current pack",
      not "you did not give one".
    * ``not_found`` → **404** ``NO_PENDING_QUESTION``. The mission
      subtree was scanned and nobody holds a pack for this work unit.

    Raises:
        fastapi.HTTPException: always.
    """
    from fastapi import HTTPException

    from daemon.models.common import ErrorResponse

    details = target.detail_fields()

    if target.error == ERROR_AMBIGUOUS:
        raise HTTPException(
            status_code=400,
            detail=ErrorResponse(
                code=ErrorCodes.INVALID_REQUEST,
                message=(
                    f"Ambiguous answer target: {len(target.candidate_ids)} "
                    f"instances in this mission hold a pending question "
                    f"({', '.join(target.candidate_ids)}) — supply "
                    f"question_pack_id to select the right one."
                ),
                details=details,
            ).model_dump(),
        )

    pack_clause = (
        f"question pack {target.pack_id_searched!r}"
        if target.pack_id_searched
        else "a question pack"
    )
    raise HTTPException(
        status_code=404,
        detail=ErrorResponse(
            code=ErrorCodes.NO_PENDING_QUESTION,
            message=(
                f"No instance in this mission holds {pack_clause} — the "
                f"mission subtree was scanned and no pack-holding asker "
                f"was found (work instance {target.work_instance_id}, "
                f"mission root {target.mission_root_id})."
            ),
            details=details,
        ).model_dump(),
    )


__all__ = [
    "AnswerTarget",
    "ERROR_AMBIGUOUS",
    "ERROR_NOT_FOUND",
    "MAX_SUBTREE_SCAN",
    "ROUTE_DIRECT_METADATA_SHADOW",
    "ROUTE_DIRECT_RAM_PACK",
    "ROUTE_SUBTREE_ANSWER_HANDLE",
    "ROUTE_SUBTREE_PACK_ID",
    "raise_unresolved_target",
    "resolve_answer_target",
]
