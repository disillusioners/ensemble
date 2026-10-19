"""Job-scoped answer-target resolution (2026-10-19; restructured 2026-10-20).

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

Resolution (one shared helper — both surfaces call it)
-------------------------------------------------------

**SINGLE candidate-collection pass.** Every instance of the reachable
set is probed ONCE for the two live signals, and the collected
candidates are then arbitrated. The resolver never returns a candidate
before the whole set has been collected — an early return is exactly
what made the "root wins unconditionally" ambiguity hole (the root
short-circuited the scan, so a second asker was never seen).

A candidate is an instance that either

* **(P)** holds a LIVE PENDING RAM pack — the question is open right
  now (this also covers the PRE-PAUSE window: ``question_tools.py``
  stores the pack and stamps the durable shadow BEFORE the
  ``awaiting_answer`` pause fires, so a child that asked a moment ago
  has a live pack but no handle yet), or
* **(H)** holds the AUTHORITATIVE awaiting-answer handle — the task is
  ``status='paused' AND suspension_reason='awaiting_answer' AND
  resume_target_turn_id IS NOT NULL`` (reused from
  ``TaskRepository.find_suspended_turn_for_answer``). The handle is the
  authoritative "still waiting" signal — it is what the v0.25.3 boot
  rehydrate pass keys on (``daemon/manager.py``
  ``_rehydrate_question_packs_at_boot``), so the durable-shadow path
  stays reachable through it.

A durable ``question_pack_payload`` shadow on its own is NOT a
candidate: the shadow is a FROZEN ask-time snapshot (``status`` is
whatever it was when the question was asked), so a stale PENDING
shadow on the mission root would otherwise win the resolution and hide
the LIVE question of a child. The shadow is used for two things only:
(1) the pack id of a handle candidate (so the supplied pack id can
select it), and (2) the per-instance rehydrate source inside
``answer_helper._resolve_pack_or_raise`` (unchanged, v0.25.3 path).

Arbitration:

* **Pack id supplied** — the STRONGEST key. The candidate whose pack
  id matches the supplied id wins. If the supplied id matches NOBODY,
  the remaining candidates are used as the fallback so the per-instance
  error shape (mismatch / lost / no-pending) is produced by the asker
  that actually holds a pack.
* **No pack id** — the collected candidates decide. Exactly one → it
  is the asker. SEVERAL → 400 ``INVALID_REQUEST`` naming them: we
  never pick one by recency (answering the WRONG question is worse
  than refusing). NONE → 404 ``NO_PENDING_QUESTION`` (or 410
  ``ANSWER_TARGET_TERMINAL`` when the work instance itself is
  terminal — that signal must survive the resolution).

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
from daemon.services.midflight_qa import QUESTION_PACK_ID_METADATA_KEY

if TYPE_CHECKING:
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)


# ── Tunables ────────────────────────────────────────────────────────────

# Hard cap on the candidate-collection pass when the caller supplied NO
# pack id. The pass reads one task row per instance (the handle), so an
# unbounded scan over a huge mission would be unbounded work. Mission
# trees are small (a leader plus a handful of children); the cap only
# bounds a pathological fan-out.
#
# The cap is LIFTED when a pack id IS supplied: the pack id is the
# strongest key, so the caller has told us exactly which pack to
# deliver — truncating the scan before reaching the holder would
# produce a false 404 for an asker that sits beyond the cap. The
# enumeration itself stays bounded by the repository's own
# ``_MAX_TRAVERSAL_DEPTH`` (256) in ``get_tree_ids_permanent``.
MAX_SUBTREE_SCAN = 64


# ── Resolution routes (diagnostic — surfaced in the success envelope) ──

ROUTE_DIRECT_RAM_PACK = "direct_ram_pack"
ROUTE_DIRECT_METADATA_SHADOW = "direct_metadata_shadow"
ROUTE_SUBTREE_PACK_ID = "subtree_pack_id"
ROUTE_SUBTREE_PENDING_PACK = "subtree_pending_pack"
ROUTE_SUBTREE_ANSWER_HANDLE = "subtree_answer_handle"


# ── Error kinds ─────────────────────────────────────────────────────────

ERROR_AMBIGUOUS = "ambiguous"
ERROR_NOT_FOUND = "not_found"
ERROR_TERMINAL = "terminal"


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
        subtree_scanned: True when the candidate-collection pass ran.
        subtree_truncated: True when the pass hit ``MAX_SUBTREE_SCAN``
            (only possible without a supplied pack id).
        candidate_ids: The candidate instance ids that were considered
            (populated for the ambiguous case).
        pack_id_searched: The pack id the caller asked for (may be
            ``None``).
        error: ``None`` on success, else ``"ambiguous"`` (several
            candidates, no pack id to disambiguate), ``"not_found"``
            (nobody in the mission holds a pack) or ``"terminal"``
            (the work instance itself is terminal).
        work_instance_status: The work instance's status when it was
            read (diagnostic).
    """

    asker_instance_id: str | None
    route: str
    work_instance_id: str
    mission_root_id: str
    subtree_scanned: bool = False
    subtree_truncated: bool = False
    candidate_ids: tuple[str, ...] = ()
    pack_id_searched: str | None = None
    error: str | None = None
    work_instance_status: str | None = None

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
            "subtree_truncated": self.subtree_truncated,
            "resolution_route": self.route or None,
            "candidate_ids": list(self.candidate_ids),
            "work_instance_status": self.work_instance_status,
        }


@dataclass(frozen=True)
class _Candidate:
    """One instance of the mission that may be the asker.

    Attributes:
        instance_id: The candidate instance.
        pack_id: The pack id this instance holds (the live RAM pack id,
            or the durable shadow id for a handle-only candidate).
        live_pack: A LIVE PENDING RAM pack exists on this instance.
        has_handle: The authoritative awaiting-answer handle exists.
    """

    instance_id: str
    pack_id: str | None
    live_pack: bool
    has_handle: bool


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


def _is_pending_pack(pack: Any) -> bool:
    """True when ``pack`` is a LIVE PENDING question pack.

    A RAM pack SURVIVES answer consumption (only the durable metadata
    shadow is cleared at consumption), so an ANSWERED pack can sit on
    the mission root long after its own question was answered.
    Short-circuiting on it routes the answer at the wrong instance and
    the CAS then LOSES — a silent 200 ``already_delivered`` while the
    LIVE question is never answered.
    """
    if pack is None:
        return False
    if _pack_id_of(pack) is None:
        return False
    return getattr(pack, "status", None) == "pending"


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


def _read_answer_handle(task_repo: Any, instance_id: str) -> Any:
    """Read the authoritative awaiting-answer handle of ``instance_id``.

    Returns the task row, ``None`` when there is none, and ``True`` as
    the "ambiguous duplicates" marker when the repository refuses
    because several suspended answer-gate rows exist (an invariant
    violation — surfaced, never resolved by recency).
    """
    if task_repo is None:
        return None
    try:
        return task_repo.find_suspended_turn_for_answer(instance_id)
    except ValueError:
        logger.warning(
            "answer_resolver: duplicate awaiting-answer handles for %s "
            "— treating as an ambiguous candidate",
            instance_id[:8],
        )
        return True
    except Exception as e:  # noqa: BLE001 — best-effort read
        logger.warning(
            "answer_resolver: answer-handle lookup failed for %s: %s",
            instance_id[:8],
            e,
        )
        return None


def _read_instance_status(instance_repo: Any, instance_id: str) -> str | None:
    """Read an instance's status (diagnostic + terminal pre-check)."""
    if instance_repo is None:
        return None
    try:
        row = instance_repo.get(instance_id)
    except Exception as e:  # noqa: BLE001 — best-effort read
        logger.warning(
            "answer_resolver: instance status read failed for %s: %s",
            instance_id[:8],
            e,
        )
        return None
    status = getattr(row, "status", None)
    return status if isinstance(status, str) else None


# ── The resolver ────────────────────────────────────────────────────────


def _reachable_set(
    instance_repo: Any,
    mission_root_id: str,
    work_instance_id: str,
) -> list[str]:
    """The reachable target set — WIDENED, but still mission-scoped.

    The set is the PERMANENT lineage of the mission root (the root plus
    every descendant, ``instances.parent_id`` — the lineage survives
    completion / error / terminate / revive), PLUS the work instance
    itself.

    * The mission ROOT is folded in explicitly: the work unit may
      resolve to a CHILD (the mission receipt the watcher holds), while
      the question may have been asked by the root. Keying the direct
      lanes on the work instance alone made the root's own pending pack
      unreachable.
    * ``get_tree_ids_permanent`` is called DIRECTLY (not through
      ``get_cascade_tree_ids``): the ``ENSEMBLE_CASCADE_LINEAGE``
      kill-switch selects between the permanent lineage and the
      TRANSIENT ``instance_hierarchy`` working set, and the transient
      set silently loses churned descendants. The resolver's lineage
      semantics must not change with that switch — an answer that was
      routed to the wrong instance is a data-loss class, not a
      performance knob.
    * The set stays inside ONE mission (one project by construction):
      the permanent lineage of the mission root never leaves it.
    """
    ids: list[str] = []
    if instance_repo is not None:
        try:
            ids = list(
                instance_repo.get_tree_ids_permanent(mission_root_id) or []
            )
        except Exception as e:  # noqa: BLE001 — degrade to the work id
            logger.warning(
                "answer_resolver: permanent-lineage enumeration failed "
                "for %s: %s",
                mission_root_id[:8],
                e,
            )
    ordered: list[str] = []
    for iid in (mission_root_id, work_instance_id):
        if iid and iid not in ordered:
            ordered.append(iid)
    for iid in ids:
        if iid not in ordered:
            ordered.append(iid)
    return ordered


def _collect_candidates(
    instance_repo: Any,
    qm: Any,
    task_repo: Any,
    reachable: list[str],
) -> list[_Candidate]:
    """ONE pass over the reachable set — collect every possible asker.

    Per instance: the live pending RAM pack (in-memory, free) and the
    authoritative awaiting-answer handle (one indexed task read). The
    durable shadow is read ONLY for a handle candidate (its pack id is
    what lets a supplied pack id select it).
    """
    candidates: list[_Candidate] = []
    for iid in reachable:
        ram_pack = qm.get_question_pack(iid) if qm is not None else None
        if _is_pending_pack(ram_pack):
            handle = _read_answer_handle(task_repo, iid)
            candidates.append(
                _Candidate(
                    instance_id=iid,
                    pack_id=_pack_id_of(ram_pack),
                    live_pack=True,
                    has_handle=_is_answer_handle(handle) or handle is True,
                )
            )
            continue
        handle = _read_answer_handle(task_repo, iid)
        if _is_answer_handle(handle) or handle is True:
            candidates.append(
                _Candidate(
                    instance_id=iid,
                    pack_id=_read_shadow_id(instance_repo, iid),
                    live_pack=False,
                    has_handle=True,
                )
            )
    return candidates


def _route_for(
    candidate: _Candidate,
    work_instance_id: str,
    matched_by_pack_id: bool,
) -> str:
    """Name HOW the winning candidate was identified."""
    if matched_by_pack_id:
        return ROUTE_SUBTREE_PACK_ID
    if candidate.instance_id == work_instance_id and candidate.live_pack:
        return ROUTE_DIRECT_RAM_PACK
    if candidate.has_handle:
        return ROUTE_SUBTREE_ANSWER_HANDLE
    return ROUTE_SUBTREE_PENDING_PACK


def resolve_answer_target(
    manager: "InstanceManager",
    work_instance_id: str,
    question_pack_id: str | None = None,
) -> AnswerTarget:
    """Resolve the ASKING instance for a job-scoped answer request.

    The REACHABLE TARGET SET is the permanent lineage of the mission
    root (the root + every descendant) plus the work instance itself —
    WIDENED (the root's own pending pack must be reachable even when
    the work resolves to a child) but still MISSION-SCOPED: the lineage
    of one mission root never leaves that mission (one project by
    construction). See :func:`_reachable_set`.

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
        set; otherwise ``error`` is ``"ambiguous"``, ``"not_found"`` or
        ``"terminal"`` and ``candidate_ids`` carries the evidence.
    """
    instance_repo = getattr(manager, "_instance_repository", None)
    qm = getattr(manager, "_question_manager", None)
    task_repo = getattr(manager, "_task_repo", None)

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

    # ── FAST PATH — the work instance holds the pack the caller means ─
    # A supplied pack id is the STRONGEST key: it identifies exactly
    # one pack, so no ambiguity is possible and the candidate pass can
    # be skipped entirely (zero repository reads). The pack id is a
    # uuid4 — two instances holding ONE pack id is not a reachable
    # state, so the pack-id uniqueness scan the general path performs
    # cannot change the outcome here.
    if question_pack_id is not None:
        ram_pack = (
            qm.get_question_pack(work_instance_id) if qm is not None else None
        )
        if (
            _is_pending_pack(ram_pack)
            and _pack_id_of(ram_pack) == question_pack_id
        ):
            logger.debug(
                "answer_resolver: fast path — work instance %s holds "
                "the requested pack %s",
                work_instance_id[:8],
                question_pack_id,
            )
            return AnswerTarget(
                asker_instance_id=work_instance_id,
                route=ROUTE_DIRECT_RAM_PACK,
                work_instance_id=work_instance_id,
                mission_root_id=mission_root_id,
                subtree_scanned=False,
                pack_id_searched=question_pack_id,
            )

    # ── REACHABLE SET (widened, mission-scoped) ──────────────────────
    reachable = _reachable_set(instance_repo, mission_root_id, work_instance_id)

    # The cap is lifted when a pack id was supplied — the caller named
    # the pack, so truncating the scan before reaching its holder would
    # produce a false 404 for an asker that sits beyond the cap.
    truncated = False
    if question_pack_id is None and len(reachable) > MAX_SUBTREE_SCAN:
        logger.warning(
            "answer_resolver: candidate scan capped at %d instances "
            "(mission %s has %d) — an asker beyond the cap may be missed",
            MAX_SUBTREE_SCAN,
            mission_root_id[:8],
            len(reachable),
        )
        reachable = reachable[:MAX_SUBTREE_SCAN]
        truncated = True

    # ── SINGLE CANDIDATE-COLLECTION PASS ─────────────────────────────
    candidates = _collect_candidates(instance_repo, qm, task_repo, reachable)

    def _resolved(
        candidate: _Candidate, matched_by_pack_id: bool
    ) -> AnswerTarget:
        return AnswerTarget(
            asker_instance_id=candidate.instance_id,
            route=_route_for(candidate, work_instance_id, matched_by_pack_id),
            work_instance_id=work_instance_id,
            mission_root_id=mission_root_id,
            subtree_scanned=True,
            subtree_truncated=truncated,
            pack_id_searched=question_pack_id,
        )

    def _ambiguous(candidate_ids: list[str]) -> AnswerTarget:
        logger.warning(
            "answer_resolver: %d instances of mission %s can be the "
            "asker (%s) — refusing to guess",
            len(candidate_ids),
            mission_root_id[:8],
            ", ".join(i[:8] for i in candidate_ids),
        )
        return AnswerTarget(
            asker_instance_id=None,
            route="",
            work_instance_id=work_instance_id,
            mission_root_id=mission_root_id,
            subtree_scanned=True,
            subtree_truncated=truncated,
            candidate_ids=tuple(candidate_ids),
            pack_id_searched=question_pack_id,
            error=ERROR_AMBIGUOUS,
        )

    # ── ARBITRATION ──────────────────────────────────────────────────
    if question_pack_id is not None:
        matched = [c for c in candidates if c.pack_id == question_pack_id]
        if len(matched) == 1:
            return _resolved(matched[0], matched_by_pack_id=True)
        if len(matched) > 1:
            # A pack id is unique — two holders is a data anomaly. We
            # refuse to guess (the same "never pick arbitrarily" rule
            # the awaiting-answer handle path follows).
            return _ambiguous([c.instance_id for c in matched])
        # The supplied pack id matched nobody → fall back to the
        # remaining candidates so the per-instance error shape
        # (mismatch / lost / no-pending) is produced by the asker that
        # actually holds a pack.
        others = [c for c in candidates if c.pack_id != question_pack_id]
        if len(others) == 1:
            return _resolved(others[0], matched_by_pack_id=False)
        if len(others) > 1:
            return _ambiguous([c.instance_id for c in others])
    else:
        if len(candidates) == 1:
            return _resolved(candidates[0], matched_by_pack_id=False)
        if len(candidates) > 1:
            # SEVERAL askers are waiting and the caller supplied no
            # pack id to disambiguate. Picking one by recency would
            # answer the WRONG question — refuse instead.
            return _ambiguous([c.instance_id for c in candidates])

    # ── NO CANDIDATE ─────────────────────────────────────────────────
    # Nobody in the mission holds a pack. Do NOT invent one and do NOT
    # claim the pack was lost — the accurate answer is "there is no
    # pack-holding asker in this mission".
    #
    # W-4: the TERMINAL signal must survive the resolution. Before the
    # asker resolution existed, the route answered the WORK instance
    # directly, and a terminal work instance raised 410
    # ANSWER_TARGET_TERMINAL from the helper's pre-check. The
    # resolution would otherwise swallow that signal behind a 404.
    work_status = _read_instance_status(instance_repo, work_instance_id)
    error = ERROR_NOT_FOUND
    if work_status in _TERMINAL_STATUSES:
        error = ERROR_TERMINAL
    return AnswerTarget(
        asker_instance_id=None,
        route="",
        work_instance_id=work_instance_id,
        mission_root_id=mission_root_id,
        subtree_scanned=True,
        subtree_truncated=truncated,
        pack_id_searched=question_pack_id,
        error=error,
        work_instance_status=work_status,
    )


# ── The one typed error both surfaces share ────────────────────────────


def raise_unresolved_target(target: AnswerTarget) -> NoReturn:
    """Raise the accurate HTTP error for an unresolved job-scoped answer.

    Three shapes, distinguished on purpose (the old code conflated them
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
    * ``terminal`` → **410** ``ANSWER_TARGET_TERMINAL``. The work
      instance itself is COMPLETED/TERMINATED — the previously
      observable terminal signal must survive the asker resolution.
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

    if target.error == ERROR_TERMINAL:
        raise HTTPException(
            status_code=410,
            detail=ErrorResponse(
                code=ErrorCodes.ANSWER_TARGET_TERMINAL,
                message=(
                    f"The work instance {target.work_instance_id} is "
                    f"{target.work_instance_status!r} — the answer "
                    f"cannot be delivered (the asker reached a terminal "
                    f"state while the question was pending)."
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


# Terminal statuses that make an answer undeliverable — the same set the
# shared helper's pre-check uses (``midflight_qa.ANSWER_TERMINAL_STATUSES``).
# ERROR/FAILED are NOT terminal here: they are revivable, so the
# "no pending question" shape stays the accurate answer for them.
def _terminal_statuses() -> frozenset[str]:
    from daemon.services.midflight_qa import ANSWER_TERMINAL_STATUSES

    return frozenset(ANSWER_TERMINAL_STATUSES)


_TERMINAL_STATUSES = _terminal_statuses()


__all__ = [
    "AnswerTarget",
    "ERROR_AMBIGUOUS",
    "ERROR_NOT_FOUND",
    "ERROR_TERMINAL",
    "MAX_SUBTREE_SCAN",
    "ROUTE_DIRECT_METADATA_SHADOW",
    "ROUTE_DIRECT_RAM_PACK",
    "ROUTE_SUBTREE_ANSWER_HANDLE",
    "ROUTE_SUBTREE_PACK_ID",
    "ROUTE_SUBTREE_PENDING_PACK",
    "raise_unresolved_target",
    "resolve_answer_target",
]
