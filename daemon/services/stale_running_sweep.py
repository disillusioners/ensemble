"""Steady-state stale-running sweep (fix/post-outage-recovery SCOPE b4, Shape B).

The status-stale defect class
-----------------------------
An instance whose WORK IS FINISHED — every task row terminal, every
message row terminal, nothing else holds the posture — yet whose
instance row still reads ``status='running'`` with a frozen
``last_activity_at``. The instance LIES about being busy: it poisons
the census and every stale-running detector downstream. No work is
lost; only the row-state is stale.

WHY THE EXISTING SEAMS MISS IT (Item-1 root cause)
--------------------------------------------------
Both existing terminalizers are EVENT-DRIVEN and each requires an
anchor the Shape-B rows do not carry:

* ``ChildReportsService._process_child_completion_and_notify_parent``
  (the parent flip / own-stamp seam) fires only when a completion
  signal ARRIVES for that instance — the drive is reached from the
  turn's own success path (``manager.resume_processing_job``), from
  a re-entry router, or from the strand healer's drive. If the turn's
  completion event was never published (a daemon death mid-turn, the
  2026-11-23 06:17–08:21Z outage window), this seam NEVER fires for
  the instance.
* ``JobFeedbackObserver._finalize_job`` fires from the
  ``instance_lifecycle`` EVENT the report/message Task publishes (or
  from the JobItem linkage when one exists). No event ⇒ no finalize.

The two backstops cover only PART of the class:

* ``BootConsistencyReconciler`` (b2, this branch) is BOOT-ONLY. Its
  ``completed_task`` family finalizes a NON-ROOT, and its
  ``deleted_trace`` family re-mints/retires a no-trace instance — but
  BOTH require the boot window. The Shape-B specimens carried
  SURVIVING terminal message_queue rows, so they were not even in the
  ``deleted_trace`` family; and in the STEADY STATE the reconciler
  never runs at all.
* ``MissionStrandHealer``'s running-zombie strand anchors its
  candidate set on ``JOIN task (latest task terminal-COMPLETED)``.
  The measured specimens 28a21d1c / 5aba1b33 / e1a631be carried NO
  task row at all (the rows were wiped while the message_queue rows
  survived) — the anchor's inner join DROPS them, so they are
  invisible to the healer forever. Its root-only posture
  (``parent_id IS NULL``) also excludes a non-root shape such as
  e1a631be (parent 475a2bed…).

So the class has NO steady-state firing point. This lane is that
missing steady-state seam.

DESIGN
------
A bounded house-watchdog sweep (interval-driven, first tick
IMMEDIATE, fail-loud, never-wedge). One disposition: drive the
PRODUCTION completion chain
(``child_reports._process_child_completion_and_notify_parent`` — the
same drive the healer's ``finalize_gap`` uses, C5 ``""`` message-id
pin included) — NEVER a raw status UPDATE. The chain re-runs every
authoritative gate under TOCTOU (bus-pending in-transaction,
pending-task, live-children cross-check, pending messages, the
emission-time root-completion gate); a gate miss is a LEGITIMATE
downgrade (⇒ ``waiting_children``, bus-owned from then on) or an
``idempotency_skip`` — never forced.

DISCRIMINATOR (the load-bearing TRAP legs — an instance is a
candidate ONLY when nothing legitimately holds the posture):

1. ``status == 'running'`` (``waiting_children`` stays bus-owned —
   D14/D21; ``paused`` is operator domain — untouched; terminal rows
   are out by construction).
2. NO non-terminal task row (pending/running/paused/processing —
   ``NON_TERMINAL_TASK_STATUSES``).
3. NO message row whose status is NOT PROVEN-terminal. PROVEN-terminal
   is ``{completed, failed}`` — every OTHER value (``ready`` /
   ``processing`` / ``retrying`` / ``pending`` AND any value outside
   the ``MessageStatus`` enum) HOLDS the posture. The conservative
   unknown-status rule is deliberate: an off-shape message status
   (the c6ad0537 observation) must never satisfy "nothing holding"
   unless it is a known-terminal value.
4. NO open report injection (``PENDING``/``DEFERRED``) obligated to
   the instance.
5. NO fulfillable-pending dependency-bus watcher targeting it.
6. The canonical mission-live guard says the tree is NOT live
   (``skip_root=True``) — no live subtree member.
7. NO pending question anywhere in the tree (RAM packs, the durable
   metadata shadow, the ``awaiting_answer`` handle — the strand
   healer's exact three-leg vocabulary).
8. POSITIVE completion evidence: at least one terminal-COMPLETED task
   row (with a stamp) OR one ``completed`` message row. A shape whose
   only work artifacts are FAILED/CANCELLED is OUT OF SCOPE — driving
   the completion chain there would stamp ``completed`` over a turn
   that did not succeed; the error/terminate lanes own that shape.
9. AGE BOUND: the NEWEST completion evidence (max of the latest
   completed-task ``completed_at`` and the latest ``completed``
   message ``completed_at``) is at least ``age_bound_seconds`` old.
   Applied in PYTHON (the healer's dialect-soundness lesson: the
   naive-UTC stamps are TEXT on SQLite, so a SQL comparison is a
   STRING comparison there and is not chronologically sound).

BOUNDS: batch cap (default 25 — the healer's running-zombie cap),
tree probe bound (256 — fail-closed skip on truncation), kill-switch
(default ON, ``=0`` disables — same precedent as
``ENSEMBLE_SCHEDULED_FIRE_SUPERVISOR``), interval (default 300s,
first tick IMMEDIATE), three-layer never-wedge (per-candidate →
sweep → loop) with DISTINCT structured ERROR markers per failure
class (the N4-corrected marker discipline), and fail-closed skips
EVERYWHERE a probe cannot be evaluated (a missed heal is recoverable
on the next tick; a wrong terminal write is not).

OVERLAP with the healer: for the shapes the healer already anchors
(root + terminal-COMPLETED latest task), both lanes may drive the
same instance. That is safe BY CONSTRUCTION — the chain's
status-level idempotency guard makes the second drive an
``idempotency_skip`` no-op (the same N3 rationale: the guarded
status flip is the exactly-once authority, not the selection).

THE CHAIN IS THE NET FOR THE OLD-LIVE-DESCENDANT CLASS — NOT THIS
LANE'S LEGS
----------------------------------------------------------
A descendant whose ANCESTOR is still live is held by leg 6 (the
mission-live guard says the tree IS live) and by the bus-pending /
live-children legs. This lane therefore does NOT, and MUST NOT,
claim to cover that class: its legs are deliberately all-must-hold,
and a live ancestor is a legitimate holder of the posture.

The NET for that class is the PRODUCTION COMPLETION CHAIN itself —
the same chain this lane drives. When the ancestor's turn reaches its
own terminal state, the chain's parent/child cascade re-evaluates the
subtree and terminalizes the descendant under the chain's own
authoritative gates (bus-pending in-transaction, pending-task,
live-children cross-check, pending messages, emission-time
root-completion gate). A descendant that is old but still under a
live ancestor is therefore terminalized BY THE CHAIN, at the moment
the ancestor completes — not by this sweep's legs, which correctly
refuse to touch it while the ancestor holds.

WHY THE DISTINCTION MATTERS: a reader who believes this lane covers
the old-live-descendant class will read a still-running descendant as
a MISSED heal. It is not — it is a held posture with an owning chain.
The sweep's value is the class the chain's EVENT-DRIVEN seams cannot
reach at all (no completion event ever arrives — the daemon-death
window), NOT a second implementation of the chain's cascade.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import text
from sqlmodel import Session, func, select

from daemon.constants import NON_TERMINAL_TASK_STATUSES
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task

if TYPE_CHECKING:  # pragma: no cover
    from daemon.manager import InstanceManager

logger = logging.getLogger(__name__)

# ── Knobs (house watchdog pattern) ────────────────────────────────────────

KILL_SWITCH_ENV = "ENSEMBLE_STALE_RUNNING_SWEEP"
INTERVAL_ENV = "ENSEMBLE_STALE_RUNNING_SWEEP_INTERVAL_SECONDS"
AGE_BOUND_ENV = "ENSEMBLE_STALE_RUNNING_SWEEP_AGE_BOUND_SECONDS"
BATCH_CAP_ENV = "ENSEMBLE_STALE_RUNNING_SWEEP_BATCH_CAP"

DEFAULT_INTERVAL_SECONDS = 300

#: Age bound on the NEWEST completion evidence — same class as the
#: strand healer's running-zombie bound (``DEFAULT_RUNNING_ZOMBIE_AGE_
#: BOUND_SECONDS``). Long enough that the normal completion stamp has
#: long since landed; short enough that the shape clears within one
#: sweep tick after the bound.
DEFAULT_AGE_BOUND_SECONDS = 7200

#: Batch cap per sweep pass — the healer's running-zombie cap (25).
DEFAULT_BATCH_CAP = 25

#: Upper bound on the permanent-tree size probed per candidate — same
#: posture as ``stale_pause_sweep._MAX_TREE_PROBE_SIZE`` / the healer's
#: 256. A tree larger than this is NOT probed completely ⇒ fail-closed
#: skip (PROTECTED, never swept).
_MAX_TREE_PROBE_SIZE = 256

#: PROVEN-terminal message statuses. EVERY other value — including any
#: value outside the ``MessageStatus`` enum (the off-shape c6ad0537
#: observation) — HOLDS the posture (conservative; see leg 3).
_TERMINAL_MESSAGE_STATUSES = frozenset({"completed", "failed"})

#: Instance statuses this lane NEVER touches (beyond the ``running``
#: posture filter): ``waiting_children`` is bus-owned (D14/D21) and
#: ``paused`` is operator domain.
_NEVER_TOUCHED_STATUSES = frozenset({"waiting_children", "paused"})


class _NoHoldingMessage:
    """The sentinel for "this instance has NO holding message row".

    NIT-2 (fix/post-outage-recovery SCOPE (c) rider): the probe
    returned ``str | None`` and used ``None`` for BOTH "no message row
    at all" AND "a message row whose status column is NULL". A NULL
    status is NOT proven-terminal (``is_proven_terminal_message_status``
    returns False for it — the conservative unknown-status rule), so it
    MUST hold the posture. Under the old shape the NULL-status row read
    as "nothing holding" and the candidate was driven to a terminal
    stamp on the strength of a row the sweep itself classifies as
    holding. Unreachable today (the column is NOT NULL in the shipped
    schema) — but the leg's fail-closed posture must not depend on a
    schema invariant it does not own, so the distinction is explicit.

    The sentinel is FALSY and has a stable ``repr`` so a log line can
    never confuse it with a status string.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover — repr only
        return "<no-holding-message>"

    def __bool__(self) -> bool:  # pragma: no cover — bool only
        return False


#: The "no holding message" sentinel (see :class:`_NoHoldingMessage`).
NO_HOLDING_MESSAGE = _NoHoldingMessage()

#: The ``instance_metadata`` key carrying the typed terminal-transition
#: reason (NIT-6). Content-free: a short vocabulary token, never free
#: text and never a transcript excerpt.
TERMINAL_REASON_METADATA_KEY = "terminal_transition_reason"

#: The typed reason this lane stamps when it drives a row to terminal.
TERMINAL_REASON_VALUE = "finalize_gap_steady_state"



def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "[STALE_RUNNING_SWEEP] non-numeric %s=%r — using default %d",
            name, raw, default,
        )
        return default


def kill_switch_enabled() -> bool:
    """Default ON; ``=0`` (or false/no/off) disables."""
    raw = os.environ.get(KILL_SWITCH_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off")


def is_proven_terminal_message_status(status: str | None) -> bool:
    """True iff the message status PROVES the row is terminal.

    The conservative unknown-status rule: only ``completed`` and
    ``failed`` prove terminality. ``pending``/``ready``/``processing``/
    ``retrying`` hold, and ANY value outside the known vocabulary holds
    too — an unknown status must never satisfy "nothing holding"
    unless proven terminal.
    """
    return (status or "") in _TERMINAL_MESSAGE_STATUSES


@dataclass
class SweepResult:
    """Per-sweep totals. Every counter zero-initialized for fast paths."""

    candidates: int = 0
    #: driven through the production chain and now terminal.
    healed: int = 0
    #: the chain downgraded to ``waiting_children`` — a LEGITIMATE miss
    #: (bus-owned from then on; D14/D21).
    gate_downgraded: int = 0
    #: the chain ran but the row is still not terminal — re-examined
    #: next tick (bounded by the cap).
    drive_no_effect: int = 0
    skipped_non_terminal_task: int = 0
    skipped_holding_message: int = 0
    skipped_open_injection: int = 0
    skipped_bus_pending: int = 0
    skipped_live_tree: int = 0
    skipped_member_live_task: int = 0
    skipped_pending_question: int = 0
    skipped_young: int = 0
    skipped_posture_changed: int = 0
    #: tree probe truncated / unevaluatable — fail-closed PROTECTED.
    skipped_tree_truncated: int = 0
    #: any probe raised — fail-closed skip, loud ERROR marker.
    skipped_probe_error: int = 0
    skipped_kill_switch: int = 0
    errors: int = 0
    duration_seconds: float = 0.0
    mode: str = "unknown"


class StaleRunningSweepService:
    """Steady-state terminalizer for the Shape-B status-stale class."""

    def __init__(
        self,
        manager: "InstanceManager",
        *,
        interval_seconds: int | None = None,
    ) -> None:
        self._manager = manager
        self._interval_seconds = (
            interval_seconds
            if interval_seconds is not None
            else DEFAULT_INTERVAL_SECONDS
        )
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._locks: dict[str, asyncio.Lock] = {}

    # ── Lifecycle (mirrors ScheduledFireSupervisor) ────────────────────────

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(
                self._run(), name="stale-running-sweep",
            )

    async def stop(self) -> None:
        self._stopping = True
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _run(self) -> None:
        # First tick IMMEDIATE (house fast-on-boot catch-up).
        while not self._stopping:
            try:
                await self.sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — loop-level isolation
                logger.error(
                    "[STALE_RUNNING_SWEEP_LOOP_ERROR] %s: %s",
                    type(exc).__name__, exc,
                )
            try:
                await asyncio.sleep(self._interval_seconds)
            except asyncio.CancelledError:
                break

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _log_db_error(pass_name: str, result: SweepResult, exc: BaseException, **ctx: Any) -> None:
        detail = " ".join(f"{k}={v}" for k, v in ctx.items())
        logger.error(
            "[STALE_RUNNING_SWEEP_DB_ERROR] pass=%s errors=%d %s: %s: %s",
            pass_name, result.errors, detail, type(exc).__name__, exc,
        )

    @staticmethod
    def _now_naive() -> datetime:
        from daemon.services.timestamps import now_utc_naive
        return now_utc_naive()

    def _acquire_lock(self, instance_id: str) -> asyncio.Lock:
        lock = self._locks.get(instance_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[instance_id] = lock
        return lock

    # ── Selection (the SQL anchor) ─────────────────────────────────────────

    def _select_candidates(self, cap: int) -> list[dict[str, Any]]:
        """100% DB-derived candidate set — the anchored SQL selection.

        Every leg that is expressible in one portable statement is here;
        the age bound and the tree/question legs run in PYTHON (the
        healer's dialect-soundness lesson: naive-UTC stamps are TEXT on
        SQLite, so a SQL datetime comparison is a STRING comparison
        there). Read-only — no row mutation.

        An unknown message status is NOT selectable: the holding-message
        predicate is ``status NOT IN (completed, failed)`` — every value
        outside that pair (ready/processing/retrying/pending AND any
        off-shape value) keeps the instance out of the candidate set.
        """
        non_terminal_tasks = ",".join(
            f"'{s}'" for s in sorted(NON_TERMINAL_TASK_STATUSES)
        )
        terminal_msgs = ",".join(
            f"'{s}'" for s in sorted(_TERMINAL_MESSAGE_STATUSES)
        )
        statement = text(
            f"""
            SELECT
                i.instance_id AS instance_id,
                (SELECT t2.completed_at FROM task t2
                 WHERE t2.instance_id = i.instance_id
                   AND t2.status = 'completed'
                   AND t2.completed_at IS NOT NULL
                 ORDER BY t2.completed_at DESC, t2.id DESC LIMIT 1
                ) AS task_completed_at,
                (SELECT m2.completed_at FROM message_queue m2
                 WHERE m2.instance_id = i.instance_id
                   AND m2.status = 'completed'
                 ORDER BY m2.completed_at DESC LIMIT 1
                ) AS message_completed_at,
                i.parent_id AS parent_id,
                i.agent_id AS agent_id,
                i.version AS version
            FROM instances i
            WHERE i.status = 'running'
              AND NOT EXISTS (
                  SELECT 1 FROM task t
                  WHERE t.instance_id = i.instance_id
                    AND t.status IN ({non_terminal_tasks})
              )
              AND NOT EXISTS (
                  SELECT 1 FROM message_queue mq
                  WHERE mq.instance_id = i.instance_id
                    AND mq.status NOT IN ({terminal_msgs})
              )
              AND (
                  EXISTS (
                      SELECT 1 FROM task t3
                      WHERE t3.instance_id = i.instance_id
                        AND t3.status = 'completed'
                        AND t3.completed_at IS NOT NULL
                  )
                  OR EXISTS (
                      SELECT 1 FROM message_queue m3
                      WHERE m3.instance_id = i.instance_id
                        AND m3.status = 'completed'
                  )
              )
            ORDER BY
                -- NIT-4 (fix/post-outage-recovery SCOPE (c) rider):
                -- the ORDER BY must rank by the SAME evidence the
                -- PYTHON age leg uses — the NEWEST completion stamp
                -- (max of the completed-task stamp and the completed
                -- message stamp). The old shape ranked by
                -- COALESCE(task_completed_at, message_completed_at):
                -- that is the OLDEST of the two when the task stamp
                -- exists. A row whose task evidence is OLD but whose
                -- message evidence is NEW therefore sorted to the FRONT
                -- of the candidate window, consumed a cap slot, and
                -- was then skipped by the age leg ("young") — starving
                -- a genuinely eligible row behind it (the cap
                -- starvation). Ranking by the NEWEST evidence puts the
                -- eligible rows first.
                --
                -- PORTABLE MAX over two TEXT columns: both stamps are
                -- naive-UTC SQLAlchemy DateTime columns with the SAME
                -- storage format ("YYYY-MM-DD HH:MM:SS.ffffff"), so the
                -- lexicographic comparison IS chronological (the
                -- healer's dialect-soundness lesson — no datetime
                -- function, so SQLite and PostgreSQL agree). The CASE
                -- expression is the two-argument max that both engines
                -- implement (``MAX(a, b)`` is an aggregate in PG).
                CASE
                    WHEN task_completed_at IS NULL THEN message_completed_at
                    WHEN message_completed_at IS NULL THEN task_completed_at
                    WHEN task_completed_at >= message_completed_at
                        THEN task_completed_at
                    ELSE message_completed_at
                END ASC
            LIMIT :cap
            """
        )
        with self._manager.engine.connect() as conn:
            rows = conn.execute(statement, {"cap": int(cap)}).mappings().all()
        return [dict(row) for row in rows]

    def _count_non_terminal_tasks(self, instance_id: str) -> int:
        with Session(self._manager.engine) as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(Task)
                    .where(Task.instance_id == instance_id)
                    .where(Task.status.in_(NON_TERMINAL_TASK_STATUSES))
                ) or 0
            )

    def _holding_message(self, instance_id: str) -> str | None | _NoHoldingMessage:
        """The FIRST message status that holds the posture.

        Conservative: any status that is not PROVEN-terminal holds —
        the leg returns the offending status so the skip log can name
        it (including an off-shape value verbatim).

        NIT-2 (fix/post-outage-recovery SCOPE (c) rider): the return
        value is now THREE-valued. ``NO_HOLDING_MESSAGE`` means "no
        message row holds the posture"; a ``str`` means "this status
        holds"; ``None`` means "a message row's status column is NULL"
        — which is NOT proven-terminal and therefore HOLDS. The old
        two-valued shape (``str | None``) could not express the third
        case, so a NULL status silently read as "nothing holding" and
        the leg failed OPEN. The caller MUST compare against
        :data:`NO_HOLDING_MESSAGE` (never against ``None``).
        """
        with Session(self._manager.engine) as session:
            rows = session.exec(
                select(MessageQueue.status)
                .where(MessageQueue.instance_id == instance_id)
            ).all()
        for row in rows:
            status = row[0] if isinstance(row, tuple) else row
            if not is_proven_terminal_message_status(status):
                # ``status`` may be None (a NULL column) — that is a
                # HOLDING row, distinct from "no row at all".
                return status
        return NO_HOLDING_MESSAGE

    def _count_open_report_injections(self, instance_id: str) -> int:
        from daemon.repositories.report_injection.models import (
            ReportInjection,
            ReportInjectionState,
        )
        with Session(self._manager.engine) as session:
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(ReportInjection)
                    .where(ReportInjection.parent_instance_id == instance_id)
                    .where(
                        ReportInjection.state.in_([
                            ReportInjectionState.PENDING.value,
                            ReportInjectionState.DEFERRED.value,
                        ])
                    )
                ) or 0
            )

    def _bus_pending(self, instance_id: str) -> int | None:
        """Fulfillable-pending bus watchers targeting the instance.

        ``None`` = the probe could not be evaluated (fail-closed skip).
        """
        from daemon.services.dependency_bus import get_dependency_bus
        bus = get_dependency_bus()
        if bus is None:
            return None
        try:
            return int(
                bus.count_fulfillable_pending_for_target_sync(instance_id)
            )
        except Exception:  # noqa: BLE001 — fail-closed
            return None

    def _tree_ids(self, instance_id: str) -> list[str] | None:
        """The permanent-tree member ids (None = probe failed)."""
        repo = getattr(self._manager, "_instance_repository", None)
        if repo is None:
            return None
        try:
            ids = repo.get_tree_ids_permanent(instance_id)
        except Exception:  # noqa: BLE001 — fail-closed
            return None
        return list(dict.fromkeys([instance_id, *(ids or [])]))

    def _tree_is_closed(self, probe_ids: list[str]) -> tuple[bool, str]:
        """Detect DEPTH truncation of the permanent-tree enumeration.

        Mirrors the strand healer's closure check exactly
        (``MissionStrandHealer._tree_is_closed``): each member's
        permanent child list must be inside the enumerated set — a
        missing child means the walk was depth-truncated ⇒ fail-closed
        skip. An ANCESTOR outside the set is NOT truncation (normal
        for a member-scoped enumeration of a non-root candidate).
        """
        repo = getattr(self._manager, "_instance_repository", None)
        if repo is None:
            return False, "instance repository unwired"
        try:
            member_set = set(probe_ids)
            for member_id in probe_ids:
                children = repo.list_child_ids_permanent(member_id)
                for child_id in children or []:
                    if child_id not in member_set:
                        return False, (
                            f"child {child_id[:8]}... of "
                            f"{member_id[:8]}... is missing from the "
                            f"enumerated tree — the permanent-tree walk "
                            f"was depth-truncated"
                        )
            return True, "closed"
        except Exception as exc:  # noqa: BLE001 — fail-closed
            return False, f"closure probe failed: {type(exc).__name__}"

    async def _guard_verdict(self, instance_id: str):
        """The canonical mission-live guard over the tree (skip_root).

        Returns the verdict, or ``None`` when the guard could not be
        evaluated (fail-closed skip).
        """
        try:
            from daemon.services.mission_live_guard import (
                MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
                evaluate_mission_live,
            )
            return await evaluate_mission_live(
                instance_repository=self._manager._instance_repository,
                instance_id=instance_id,
                bus_pending_count=None,
                timeout_seconds=MISSION_LIVE_ORPHAN_TIMEOUT_SECONDS,
                skip_root=True,
            )
        except Exception:  # noqa: BLE001 — fail-closed
            return None

    async def _pending_question_in_tree(
        self, probe_ids: list[str], statuses: dict[str, str],
    ) -> bool | None:
        """The strand healer's three-leg pending-question probe.

        ``None`` = the probe could not be evaluated (fail-closed skip).
        """
        from daemon.constants import TERMINAL_INSTANCE_STATUSES

        def _owner_is_alive(owner_id: str) -> bool:
            return statuses.get(owner_id) not in TERMINAL_INSTANCE_STATUSES

        # (a) RAM QuestionManager packs.
        qm = getattr(self._manager, "_question_manager", None)
        if qm is not None:
            try:
                pending = await asyncio.to_thread(
                    qm.get_pending_packs_for_instances, probe_ids
                )
            except Exception:  # noqa: BLE001 — fail-closed
                return None
            for owner_id in (pending or {}):
                if _owner_is_alive(owner_id):
                    return True

        # (b) the durable metadata shadow (question_pack_payload).
        try:
            from daemon.services.midflight_qa import (
                QUESTION_PACK_PAYLOAD_METADATA_KEY,
            )
            repo = self._manager._instance_repository
            for member_id in probe_ids:
                payload = await asyncio.to_thread(
                    repo.get_metadata_value,
                    member_id,
                    QUESTION_PACK_PAYLOAD_METADATA_KEY,
                )
                if (
                    isinstance(payload, dict)
                    and payload.get("status") == "pending"
                    and _owner_is_alive(member_id)
                ):
                    return True
        except Exception:  # noqa: BLE001 — fail-closed
            return None

        # (c) the durable awaiting_answer suspension handle.
        try:
            from daemon.repositories.task.models import SuspensionReason
            from daemon.repositories.task.models import TaskStatus
            with Session(self._manager.engine) as session:
                count = int(
                    session.scalar(
                        select(func.count())
                        .select_from(Task)
                        .where(Task.instance_id.in_(list(probe_ids)))
                        .where(
                            Task.suspension_reason
                            == SuspensionReason.AWAITING_ANSWER.value
                        )
                        .where(Task.resume_target_turn_id.isnot(None))
                        .where(Task.status == TaskStatus.PAUSED.value)
                    ) or 0
                )
            return count > 0
        except Exception:  # noqa: BLE001 — fail-closed
            return None

    # ── Main sweep ─────────────────────────────────────────────────────────

    async def sweep_once(self) -> SweepResult:
        """One bounded sweep pass. Never raises out of the caller."""
        result = SweepResult()
        if not kill_switch_enabled():
            result.skipped_kill_switch = 1
            result.mode = "off"
            logger.info(
                "[STALE_RUNNING_SWEEP] OFF: %s=0 — lane skipped",
                KILL_SWITCH_ENV,
            )
            return result
        result.mode = "on"

        engine = getattr(self._manager, "engine", None)
        if engine is None:
            result.errors += 1
            self._log_db_error(
                "wiring", result, RuntimeError("manager engine unwired"),
            )
            return result

        cap = max(1, _env_int(BATCH_CAP_ENV, DEFAULT_BATCH_CAP))
        age_bound = max(1, _env_int(AGE_BOUND_ENV, DEFAULT_AGE_BOUND_SECONDS))
        loop_started = time.monotonic()
        try:
            try:
                candidates = await asyncio.to_thread(
                    self._select_candidates, cap,
                )
            except Exception as exc:  # noqa: BLE001 — fail loudly
                result.errors += 1
                self._log_db_error("selection", result, exc)
                result.duration_seconds = time.monotonic() - loop_started
                return result
            result.candidates = len(candidates)

            for candidate in candidates:
                instance_id = candidate.get("instance_id")
                if not instance_id:
                    continue
                lock = self._acquire_lock(instance_id)
                try:
                    async with lock:
                        await self._process_candidate(
                            candidate, age_bound, result,
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — per-candidate isolation
                    result.errors += 1
                    self._log_db_error(
                        "candidate", result, exc,
                        instance=str(instance_id)[:8],
                    )
        except Exception as exc:  # noqa: BLE001 — sweep-level isolation
            result.errors += 1
            self._log_db_error("sweep", result, exc)
        finally:
            result.duration_seconds = time.monotonic() - loop_started

        logger.info(
            "[STALE_RUNNING_SWEEP] summary: candidates=%d healed=%d "
            "downgraded=%d no_effect=%d skips(task=%d msg=%d inj=%d bus=%d "
            "tree=%d member_task=%d question=%d young=%d posture=%d "
            "truncated=%d probe_err=%d) errors=%d duration_s=%.3f mode=%s",
            result.candidates, result.healed, result.gate_downgraded,
            result.drive_no_effect, result.skipped_non_terminal_task,
            result.skipped_holding_message, result.skipped_open_injection,
            result.skipped_bus_pending, result.skipped_live_tree,
            result.skipped_member_live_task, result.skipped_pending_question,
            result.skipped_young, result.skipped_posture_changed,
            result.skipped_tree_truncated, result.skipped_probe_error,
            result.errors, result.duration_seconds, result.mode,
        )
        return result

    async def _process_candidate(
        self,
        candidate: dict[str, Any],
        age_bound: int,
        result: SweepResult,
    ) -> None:
        """The discriminator legs, then the production-chain drive.

        Every leg is all-must-hold and fail-CLOSED: a leg that cannot be
        evaluated SKIPS the candidate (a missed heal is recoverable on
        the next tick; a wrong terminal write is not).
        """
        instance_id = candidate.get("instance_id")

        # ── Leg 1: the posture (fresh re-read under TOCTOU) ────────────
        repo = getattr(self._manager, "_instance_repository", None)
        row = None
        if repo is not None:
            try:
                row = await asyncio.to_thread(repo.get, instance_id)
            except Exception as exc:  # noqa: BLE001 — fail-closed
                result.skipped_probe_error += 1
                logger.error(
                    "[STALE_RUNNING_SWEEP_PROBE_ERROR] instance=%s "
                    "leg=posture-read %s: %s",
                    str(instance_id)[:8], type(exc).__name__, exc,
                )
                return
        if row is None:
            result.skipped_posture_changed += 1
            logger.info(
                "[STALE_RUNNING_SWEEP] SKIP instance=%s class=posture-changed "
                "detail=row gone",
                str(instance_id)[:8],
            )
            return
        if row.status != "running":
            # waiting_children / paused / terminal — never touched here.
            result.skipped_posture_changed += 1
            logger.info(
                "[STALE_RUNNING_SWEEP] SKIP instance=%s class=posture-changed "
                "detail=status=%r (never-touched set: %s)",
                str(instance_id)[:8], row.status,
                sorted(_NEVER_TOUCHED_STATUSES),
            )
            return

        # ── Leg 2: NO non-terminal task row (TRAP: a pending/running
        #    task means a turn is in flight or waiting) ────────────────
        try:
            non_terminal = await asyncio.to_thread(
                self._count_non_terminal_tasks, instance_id,
            )
        except Exception as exc:  # noqa: BLE001 — fail-closed
            result.skipped_probe_error += 1
            logger.error(
                "[STALE_RUNNING_SWEEP_PROBE_ERROR] instance=%s "
                "leg=task-probe %s: %s",
                str(instance_id)[:8], type(exc).__name__, exc,
            )
            return
        if non_terminal > 0:
            result.skipped_non_terminal_task += 1
            logger.info(
                "[STALE_RUNNING_SWEEP] SKIP instance=%s "
                "class=non-terminal-task detail=%d non-terminal task row(s) "
                "— a turn is in flight (legitimate running)",
                str(instance_id)[:8], non_terminal,
            )
            return

        # ── Leg 3: NO message row outside the PROVEN-terminal pair
        #    (TRAP: ready/processing/retrying/pending/unknown all hold;
        #    the conservative unknown-status rule) ─────────────────────
        try:
            holding = await asyncio.to_thread(
                self._holding_message, instance_id,
            )
        except Exception as exc:  # noqa: BLE001 — fail-closed
            result.skipped_probe_error += 1
            logger.error(
                "[STALE_RUNNING_SWEEP_PROBE_ERROR] instance=%s "
                "leg=message-probe %s: %s",
                str(instance_id)[:8], type(exc).__name__, exc,
            )
            return
        if holding is not NO_HOLDING_MESSAGE:
            result.skipped_holding_message += 1
            logger.info(
                "[STALE_RUNNING_SWEEP] SKIP instance=%s class=holding-message "
                "detail=status=%r is not proven-terminal (conservative: an "
                "unknown or NULL status never satisfies 'nothing holding')",
                str(instance_id)[:8], holding,
            )
            return

        # ── Leg 4: NO open report injection (TRAP: a declared-wait
        #    obligation owns the posture) ───────────────────────────────
        try:
            open_injections = await asyncio.to_thread(
                self._count_open_report_injections, instance_id,
            )
        except Exception as exc:  # noqa: BLE001 — fail-closed
            result.skipped_probe_error += 1
            logger.error(
                "[STALE_RUNNING_SWEEP_PROBE_ERROR] instance=%s "
                "leg=injection-probe %s: %s",
                str(instance_id)[:8], type(exc).__name__, exc,
            )
            return
        if open_injections > 0:
            result.skipped_open_injection += 1
            logger.info(
                "[STALE_RUNNING_SWEEP] SKIP instance=%s "
                "class=open-report-injection detail=%d open "
                "PENDING/DEFERRED injection(s)",
                str(instance_id)[:8], open_injections,
            )
            return

        # ── Leg 5: NO fulfillable-pending bus watcher (TRAP: pending
        #    children own the posture; D14/D21) ─────────────────────────
        bus_pending = await asyncio.to_thread(self._bus_pending, instance_id)
        if bus_pending is None:
            result.skipped_probe_error += 1
            logger.error(
                "[STALE_RUNNING_SWEEP_PROBE_ERROR] instance=%s leg=bus-probe "
                "detail=dependency bus unwired or probe failed — fail-closed",
                str(instance_id)[:8],
            )
            return
        if bus_pending > 0:
            result.skipped_bus_pending += 1
            logger.info(
                "[STALE_RUNNING_SWEEP] SKIP instance=%s class=bus-pending "
                "detail=bus_pending=%d",
                str(instance_id)[:8], bus_pending,
            )
            return

        # ── Leg 6: the whole permanent tree, terminal and complete ────
        tree_ids = await asyncio.to_thread(self._tree_ids, instance_id)
        if tree_ids is None:
            result.skipped_probe_error += 1
            logger.error(
                "[STALE_RUNNING_SWEEP_PROBE_ERROR] instance=%s leg=tree-probe "
                "detail=tree enumeration failed — fail-closed",
                str(instance_id)[:8],
            )
            return
        if len(tree_ids) > _MAX_TREE_PROBE_SIZE:
            result.skipped_tree_truncated += 1
            logger.info(
                "[STALE_RUNNING_SWEEP] SKIP instance=%s class=tree-truncated "
                "detail=tree size %d exceeds the probe bound %d (PROTECTED)",
                str(instance_id)[:8], len(tree_ids), _MAX_TREE_PROBE_SIZE,
            )
            return
        closed, closure_detail = await asyncio.to_thread(
            self._tree_is_closed, tree_ids,
        )
        if not closed:
            result.skipped_tree_truncated += 1
            logger.info(
                "[STALE_RUNNING_SWEEP] SKIP instance=%s "
                "class=tree-truncated detail=%s",
                str(instance_id)[:8], closure_detail,
            )
            return

        verdict = await self._guard_verdict(instance_id)
        if verdict is None:
            result.skipped_probe_error += 1
            logger.error(
                "[STALE_RUNNING_SWEEP_PROBE_ERROR] instance=%s leg=guard "
                "detail=mission-live guard could not evaluate the tree — "
                "fail-closed (this lane terminalizes)",
                str(instance_id)[:8],
            )
            return
        if verdict.error:
            result.skipped_probe_error += 1
            logger.error(
                "[STALE_RUNNING_SWEEP_PROBE_ERROR] instance=%s leg=guard "
                "detail=%s — fail-closed",
                str(instance_id)[:8], verdict.reason,
            )
            return
        if verdict.live:
            if getattr(verdict, "member_task_hold", False):
                result.skipped_member_live_task += 1
            else:
                result.skipped_live_tree += 1
            logger.info(
                "[STALE_RUNNING_SWEEP] SKIP instance=%s class=%s detail=%s",
                str(instance_id)[:8],
                "member-live-task" if getattr(verdict, "member_task_hold", False)
                else "live-tree",
                verdict.reason,
            )
            return

        # ── Leg 7: NO pending question anywhere in the tree ───────────
        repo = getattr(self._manager, "_instance_repository", None)
        descendant_ids = [pid for pid in tree_ids if pid != instance_id]
        if descendant_ids:
            try:
                statuses = await asyncio.to_thread(
                    repo.get_statuses, descendant_ids,
                )
            except Exception as exc:  # noqa: BLE001 — fail-closed
                result.skipped_probe_error += 1
                logger.error(
                    "[STALE_RUNNING_SWEEP_PROBE_ERROR] instance=%s "
                    "leg=status-probe %s: %s",
                    str(instance_id)[:8], type(exc).__name__, exc,
                )
                return
            unreadable = [
                pid for pid in descendant_ids if pid not in (statuses or {})
            ]
            if unreadable:
                result.skipped_probe_error += 1
                logger.error(
                    "[STALE_RUNNING_SWEEP_PROBE_ERROR] instance=%s "
                    "leg=status-probe detail=%d descendant row(s) unreadable "
                    "— fail-closed",
                    str(instance_id)[:8], len(unreadable),
                )
                return
        else:
            statuses = {}
        has_question = await self._pending_question_in_tree(
            tree_ids, {instance_id: row.status, **(statuses or {})},
        )
        if has_question is None:
            result.skipped_probe_error += 1
            logger.error(
                "[STALE_RUNNING_SWEEP_PROBE_ERROR] instance=%s "
                "leg=question-probe detail=probe failed — fail-closed",
                str(instance_id)[:8],
            )
            return
        if has_question:
            result.skipped_pending_question += 1
            logger.info(
                "[STALE_RUNNING_SWEEP] SKIP instance=%s "
                "class=pending-question detail=an outstanding question in "
                "the tree still owns the mission",
                str(instance_id)[:8],
            )
            return

        # ── Leg 8/9: the age bound on the NEWEST completion evidence ──
        now_naive = await asyncio.to_thread(self._now_naive)
        stamps = [
            candidate.get("task_completed_at"),
            candidate.get("message_completed_at"),
        ]
        ages = [
            self._age_seconds(stamp, now_naive)
            for stamp in stamps
            if stamp is not None
        ]
        if not ages:
            # No readable stamp — the completion proof is unevaluatable.
            result.skipped_probe_error += 1
            logger.error(
                "[STALE_RUNNING_SWEEP_PROBE_ERROR] instance=%s leg=age-probe "
                "detail=no readable completion stamp — fail-closed",
                str(instance_id)[:8],
            )
            return
        newest_age = min(ages)  # the NEWEST evidence = the smallest age
        if newest_age < age_bound:
            result.skipped_young += 1
            logger.info(
                "[STALE_RUNNING_SWEEP] SKIP instance=%s class=young "
                "detail=newest completion evidence is %ds old (< bound %ds)",
                str(instance_id)[:8], int(newest_age), age_bound,
            )
            return

        # ── Drive the PRODUCTION completion chain ─────────────────────
        await self._drive(instance_id, candidate, result)

    @staticmethod
    def _age_seconds(stamp: Any, now_naive: datetime) -> float | None:
        """Age of a naive-UTC (or ISO-with-tz) stamp in seconds."""
        if isinstance(stamp, datetime):
            dt = stamp
        elif isinstance(stamp, str):
            try:
                dt = datetime.fromisoformat(stamp)
            except (TypeError, ValueError):
                return None
        else:
            return None
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return (now_naive - dt).total_seconds()

    async def _persist_terminal_reason(
        self, instance_id: str, reason: str
    ) -> bool:
        """Persist the TYPED transition reason onto the instance row.

        NIT-6 (fix/post-outage-recovery SCOPE (c) rider): the reason
        (``finalize_gap_steady_state``) existed ONLY in a log line, so
        the provenance of a terminal stamp this lane produced was not
        recoverable from the data — an operator reading the row could
        not tell a sweep-driven terminalization from a natural one.

        The write goes through the repository's dialect-aware atomic
        metadata path (``set_metadata`` → ``jsonb_set`` on PostgreSQL /
        ``json_set`` on SQLite), so it composes with concurrent
        metadata writes instead of clobbering the whole JSON blob (the
        ORM setattr path is a read-modify-write and is rejected by
        ``InstanceRepository.update`` for exactly this reason).

        BEST-EFFORT: a failure here is logged and never fails the
        drive — the stamp already landed, and the reason is
        provenance, not state. Returns ``True`` only when the write
        actually landed.
        """
        repo = getattr(self._manager, "_instance_repository", None)
        if repo is None:
            logger.warning(
                "[STALE_RUNNING_SWEEP_REASON_PERSIST_ERROR] instance=%s "
                "detail=instance repository unwired — reason not persisted",
                str(instance_id)[:8],
            )
            return False
        try:
            await asyncio.to_thread(
                repo.set_metadata,
                instance_id,
                TERMINAL_REASON_METADATA_KEY,
                reason,
            )
            return True
        except Exception as exc:  # noqa: BLE001 — provenance is best-effort
            logger.warning(
                "[STALE_RUNNING_SWEEP_REASON_PERSIST_ERROR] instance=%s "
                "detail=%s: %s — reason not persisted (the stamp itself "
                "already landed)",
                str(instance_id)[:8], type(exc).__name__, exc,
            )
            return False

    async def _drive(
        self,
        instance_id: str,
        candidate: dict[str, Any],
        result: SweepResult,
    ) -> None:
        """Drive the production completion chain (the healer's seam).

        NEVER a raw status UPDATE and NEVER ``terminate_instance``: the
        chain re-runs every gate authoritatively and writes the terminal
        row itself (the guarded status flip IS the exactly-once CAS —
        the N3 rationale).
        """
        child_reports = getattr(
            self._manager, "_child_reports_service", None
        )
        if child_reports is None:
            result.errors += 1
            logger.error(
                "[STALE_RUNNING_SWEEP_DRIVE_ERROR] instance=%s detail="
                "child_reports service unwired — cannot drive the production "
                "completion chain",
                str(instance_id)[:8],
            )
            return
        latest_msg_id = await asyncio.to_thread(
            self._latest_completed_message_id, instance_id,
        )
        try:
            # C5 PIN — ``completed_message_id`` MUST stay the EMPTY
            # STRING, never None (the pending-messages gate filters
            # ``message_id != completed_message_id``; in SQL
            # ``!= NULL`` is never true, which would silently disable
            # the gate).
            await child_reports._process_child_completion_and_notify_parent(
                instance_id, latest_msg_id or "", skip_repair=True,
            )
        except Exception as exc:  # noqa: BLE001 — fail-closed
            # May be POST-COMMIT (the stamp commits before some
            # post-commit side effects) — the next tick re-reads the
            # status; the status read is the verdict.
            result.drive_no_effect += 1
            logger.error(
                "[STALE_RUNNING_SWEEP_DRIVE_ERROR] instance=%s detail="
                "drive raised %s: %s (may be POST-COMMIT — the next tick "
                "re-reads the status)",
                str(instance_id)[:8], type(exc).__name__, exc,
            )
            return
        repo = getattr(self._manager, "_instance_repository", None)
        drove = None
        if repo is not None:
            try:
                drove = await asyncio.to_thread(repo.get, instance_id)
            except Exception:  # noqa: BLE001 — verdict via status read
                drove = None
        status = getattr(drove, "status", None)
        if status in ("completed", "error", "terminated", "failed"):
            result.healed += 1
            # NIT-6 — persist the typed reason onto the ROW (the log
            # line below is the human channel; the row is the durable
            # provenance channel). Best-effort: a persist failure never
            # unwinds the stamp.
            await self._persist_terminal_reason(
                instance_id, TERMINAL_REASON_VALUE,
            )
            logger.warning(
                "stale_running_sweep|shape-b: instance=%s task=%s "
                "message=%s transitioned=%s reason=%s",
                instance_id, str(candidate.get("task_completed_at")),
                str(candidate.get("message_completed_at")), status,
                TERMINAL_REASON_VALUE,
            )
            return
        if status == "waiting_children":
            # The emission gate downgraded the stamp — a LEGITIMATE
            # miss: the row is bus-owned from now on (D14/D21). NEVER
            # force; this lane does not retry it.
            result.gate_downgraded += 1
            logger.info(
                "[STALE_RUNNING_SWEEP] instance=%s class=gate-downgraded "
                "detail=post-drive status=waiting_children — the emission "
                "gate blocked the publish (legitimate miss; bus-owned)",
                str(instance_id)[:8],
            )
            return
        result.drive_no_effect += 1
        logger.info(
            "[STALE_RUNNING_SWEEP] instance=%s class=drive-no-effect "
            "detail=post-drive status=%r",
            str(instance_id)[:8], status,
        )

    def _latest_completed_message_id(self, instance_id: str) -> str | None:
        with Session(self._manager.engine) as session:
            row = session.exec(
                select(MessageQueue.message_id)
                .where(MessageQueue.instance_id == instance_id)
                .where(MessageQueue.status == "completed")
                .order_by(MessageQueue.completed_at.desc())
                .limit(1)
            ).first()
        return row

    # ── Census (read-only) ─────────────────────────────────────────────────

    def census_stale_running(self) -> int:
        """Count the CAPPED candidate set the sweep would drive this tick.

        NIT-3 (fix/post-outage-recovery SCOPE (c) rider): the NAME
        promises a census; the implementation has always been a
        CAPPED SAMPLE. The underlying selection is the same anchored
        query the sweep drives, but it carries ``LIMIT :cap`` — so the
        returned number is a LOWER BOUND on the stale-running
        population, never the population itself. Two consequences an
        operator must know:

        * a non-zero count means "at least this many" — the population
          may be larger;
        * a ZERO count means "no candidate in the FIRST ``cap`` rows
          of the anchored selection" — it is NOT proof that no
          stale-running instance exists (the cap is applied to the
          ORDER BY window, and the ordering is the newest-evidence
          ranking).

        The method name is KEPT (it is the b4 public surface and the
        census pin calls it by name); the docstring is corrected so the
        number is never read as a population. A TRUE population count
        needs an uncapped variant — deliberately NOT added here: the
        cap is what keeps the census cheap enough to call on every
        pin, and an uncapped scan of the anchored selection on a busy
        nest is exactly the cost this lane exists to avoid.

        Read-only. The census-style pin drives this before/after a
        sweep: a stale-running instance must LEAVE the count once the
        lane terminalized it.
        """
        try:
            return len(
                self._select_candidates(
                    max(1, _env_int(BATCH_CAP_ENV, DEFAULT_BATCH_CAP)),
                )
            )
        except Exception as exc:  # noqa: BLE001 — read-only, never raise
            logger.error(
                "[STALE_RUNNING_SWEEP_CENSUS_ERROR] %s: %s",
                type(exc).__name__, exc,
            )
            return -1


__all__ = [
    "StaleRunningSweepService",
    "SweepResult",
    "KILL_SWITCH_ENV",
    "INTERVAL_ENV",
    "AGE_BOUND_ENV",
    "BATCH_CAP_ENV",
    "NO_HOLDING_MESSAGE",
    "TERMINAL_REASON_METADATA_KEY",
    "TERMINAL_REASON_VALUE",
    "kill_switch_enabled",
    "is_proven_terminal_message_status",
]
