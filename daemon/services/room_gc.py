"""Agent group chat P6 — the room GC sweeper (R2: PURGE, not archive).

Architecture §2.2 (the ONE GC definition) + §13's P6 row ("GC PURGE
sweeper (R2: transactional ``room_purged`` + deletes; no archive
tables; dry-run first)").

Two triggers, one outcome (a LITERAL DB purge):

* **P-close** — ``state=closed AND closed_at < now − 14d``. The room
  was closed two weeks ago and nobody reopened it: the log is dead
  weight. Whole-room purge (the accepted-loss column of the §2.1
  capability row).
* **P-idle** — ``state=active AND <last activity> < now − 72h AND
  zero non-terminal instances among {members(state=active) ∪ owner ∪
  owner-ancestry chain}``. "Non-terminal" is the SHARED set
  (``NON_TERMINAL_INSTANCE_STATUSES_FOR_ROOMS`` — derived from the
  10-member instance enum; ``waiting`` IS alive). A room whose whole
  participant set is dead has nobody left to revive it, so the purge
  counts as a close.

**The cursor guard (P-idle ONLY).** A room holding a TAGGED row above
some participant's delivered cursor is SKIPPED: that message is still
owed to its addressee, so its log must survive the idle sweep. The
guard is permitted on P-idle only — P-close is a whole-room purge with
the accepted-loss column.

**Purge mechanics (§2.2).** ONE transaction per room: the content-free
``room_purged`` event (ids + counts only) → DELETE ``room_messages`` →
DELETE ``chatroom_members`` → DELETE ``chatrooms``. This sweeper does
NOT re-implement that transaction — it calls the EXISTING
``RoomChatService.purge_room`` → ``RoomRepository.purge_room`` path
(ONE implementation of the R2 mechanics; the sweeper owns only the
*decision*).

**Dry-run first.** §2.2/§13 require the sweeper to run DRY before it
deletes. The FIRST sweep of a deployment computes the candidate set,
reports it, and deletes NOTHING; the flag is persisted (the shared
meta KV) so a daemon restart does not silently re-arm the dry run
forever — a restart BEFORE the first sweep simply keeps the dry run
(fail-safe: a restart never purges unexpectedly).

**The latch is TWO-SIDED — and the READ side is the authoritative
one.** ONLY a read the store actually ANSWERED may pin the mirror. A
FAULTED read (or a store that is not there at all) leaves the latch
UNSET: that tick stays a dry run and the NEXT sweep RE-READS the
flag and re-decides — so one transient KV fault neither permanently
disables the purge (A-P6-25, the M1 adjudication) nor CONVERTS into
a purge (A-P6-6's fail-safe direction — observed on the PRODUCTION
path: the dry-run completion must NOT latch a flag the store never
answered). The WRITE side is deliberately different (measured, A-P6-26
truthing): the dry-run completion pins the RAM mirror FIRST and then
persists the flag best-effort — a FAILED durable write still leaves
THIS PROCESS purging on the in-RAM flag (RAM-only persistence) while
the DURABLE record stays absent, so only a RESTART re-arms the dry
run. A fault never turns a dry run into a purge: the purge is the NEXT
tick's authorized action, after a dry run that really happened.

**Fail-open posture.** The maintenance loop must never die on a GC
fault: every candidate is purged independently (one room's failure
never stops the others), and an unexpected exception degrades to a
logged warning.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from daemon.repositories.room.models import (
    NON_TERMINAL_INSTANCE_STATUSES_FOR_ROOMS,
    RoomState,
)

if TYPE_CHECKING:
    from daemon.repositories.room.service import RoomChatService

logger = logging.getLogger(__name__)

__all__ = [
    "RoomGcSweeper",
    "ROOM_GC_CLOSE_AGE_DAYS",
    "ROOM_GC_IDLE_AGE_HOURS",
    "ROOM_GC_INTERVAL_HOURS",
    "ROOM_GC_KV_CONTEXT_KEY",
    "ROOM_GC_KV_DRY_RUN_KEY",
]

#: P-close: a CLOSED room is purged 14 days after its close stamp.
ROOM_GC_CLOSE_AGE_DAYS = 14

#: P-idle: an ACTIVE room with no activity for 72 hours AND no living
#: participant is purged.
ROOM_GC_IDLE_AGE_HOURS = 72

#: The maintenance-seam interval (architecture §2.2: "6h min-interval").
ROOM_GC_INTERVAL_HOURS = 6.0

#: The shared-meta-KV partition for the sweeper's durable dry-run flag.
#: A module-level sentinel context key (the ``compare_tools_module``
#: precedent) — the flag is DAEMON-GLOBAL, not tree-scoped.
ROOM_GC_KV_CONTEXT_KEY = "room_gc_sweeper"

#: The flag's meta key: ``True`` once the dry run has happened.
ROOM_GC_KV_DRY_RUN_KEY = "dry_run_completed"

#: The ancestry-walk bound for the owner chain (the repository's own
#: walk uses the same magnitude — fail-closed on a parent cycle).
_ANCESTRY_WALK_DEPTH = 256


def _parse_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 stamp into an aware datetime (None if absent).

    The room rows carry TEXT ISO-8601 stamps (``_utc_now_iso`` — the
    house convention). A naive stamp is read as UTC; an unparseable
    stamp is ``None`` (the room is then NOT age-eligible — the
    fail-safe direction: an unreadable stamp never triggers a purge).
    """
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


class RoomGcSweeper:
    """The P6 GC sweeper — P-close + P-idle → purge (R2).

    One instance per manager. The sweeper is POLICY; the repository is
    mechanics; the purge transaction is the EXISTING
    ``RoomRepository.purge_room`` (never re-implemented here).
    """

    def __init__(
        self,
        *,
        manager: Any,
        logger: logging.Logger | None = None,
        kv_repo: Any = None,
        kv_context_key: str = ROOM_GC_KV_CONTEXT_KEY,
    ) -> None:
        self._manager = manager
        self._log = logger if logger is not None else logging.getLogger(__name__)
        self._kv_repo = kv_repo
        self._kv_context_key = kv_context_key
        #: RAM mirror of the durable dry-run flag (the KV is the
        #: durable truth; this avoids a KV read on every sweep).
        #:
        #: The mirror pins on a SUCCESSFUL read only — a faulted read
        #: leaves the latch unset so the next sweep re-reads (A-P6-25,
        #: M1) — and also pins when the dry run COMPLETES (the write
        #: path: the in-RAM flag holds even when the durable write
        #: fails; a restart re-arms the dry run). The write path is
        #: reached ONLY after an authoritative read (A-P6-6's
        #: fail-safe: a faulted read never latches a decision the
        #: store never backed).
        self._dry_run_done = False
        self._dry_run_loaded = False
        #: Whether the LAST flag resolution came from an AUTHORITATIVE
        #: read — the store actually answered it. ``False`` after a
        #: faulted read (or with no store at all): the sweep stays a
        #: dry run, the latch stays unset, and the next sweep re-reads
        #: and re-decides.
        self._dry_run_read_authoritative = False

    # ── the durable dry-run flag ──────────────────────────────────────

    def _load_dry_run_flag(self) -> bool:
        """Read the durable "the dry run already happened" flag.

        The READ side of the latch (A-P6-25, the M1 adjudication): the
        mirror pins on a SUCCESSFUL read only — a FAILED read does not
        latch, so the next sweep RETRIES the read and the durable truth
        becomes visible as soon as the fault clears (a
        latched-from-fault mirror would answer every later sweep from a
        stale RAM value no successful read ever backed). During the
        uncertainty the sweep stays a dry run — the same fail-safe
        direction as the dry-run-first posture itself (A-P6-6).

        The AUTHORITATIVE flag is tracked in
        ``self._dry_run_read_authoritative``: it is ``True`` only when
        the store actually ANSWERED the read (or when the mirror
        already holds a value a previous authoritative read backed). A
        faulted read — and a store that is not there at all — leaves it
        ``False``, and ``execute()`` then refuses to complete the dry
        run (no latch) so the next sweep re-reads and re-decides.

        (The WRITE side differs by design — see
        :meth:`_mark_dry_run_done`: the mirror pins there
        unconditionally, before the best-effort durable write.)
        """
        if self._dry_run_loaded:
            # The mirror already holds a value an authoritative read
            # backed (a successful read, or the dry-run completion
            # that followed one).
            self._dry_run_read_authoritative = True
            return self._dry_run_done
        repo = self._kv_repo
        if repo is None:
            repo = getattr(self._manager, "_shared_meta_kv_repo", None)
        if repo is None:
            # No durable store at all: there is NO authoritative read.
            # The sweep stays a dry run and the latch stays unset (the
            # next sweep re-reads and re-decides) — a deployment with
            # no flag store NEVER purges (the fail-safe direction: the
            # GC leaks rather than purges without a durable dry-run
            # record).
            self._dry_run_done = False
            self._dry_run_read_authoritative = False
            self._log.warning(
                "room gc: no durable dry-run flag store — treating this "
                "sweep as a dry run (fail-safe); the read is RETRIED on "
                "the next sweep (the latch pins on an authoritative read "
                "only)",
            )
            return False
        try:
            values = repo.get_all_as_dict(self._kv_context_key)
        except Exception as exc:  # noqa: BLE001 — the flag is advisory
            self._log.warning(
                "room gc: dry-run flag read failed (%r) — treating this "
                "sweep as a dry run (fail-safe); the read is RETRIED on "
                "the next sweep (the latch pins on success only)",
                exc,
            )
            self._dry_run_done = False
            self._dry_run_read_authoritative = False
            return False
        # SUCCESS — only now is the RAM mirror pinned.
        self._dry_run_done = bool(values.get(ROOM_GC_KV_DRY_RUN_KEY))
        self._dry_run_loaded = True
        self._dry_run_read_authoritative = True
        return self._dry_run_done

    def _mark_dry_run_done(self) -> None:
        """Mark the dry run done: pin the RAM mirror, persist best-effort.

        The WRITE side of the latch (measured behavior, A-P6-26
        truthing — deliberately DIFFERENT from the read side): the RAM
        mirror is pinned FIRST and UNCONDITIONALLY, then the durable
        KV write runs best-effort. A FAILED durable write therefore
        leaves THIS PROCESS purging on the in-RAM flag (RAM-only
        persistence — the next tick purges without re-reading the KV);
        only a RESTART (fresh mirror) re-arms the dry run — the
        fail-safe direction.

        The caller decides WHETHER the dry run really happened: this
        method is reached only after an AUTHORITATIVE read (a dry run
        the store backed), never after a faulted one (A-P6-6's
        fail-safe — a fault never converts a dry run into a purge).
        """
        self._dry_run_done = True
        self._dry_run_loaded = True
        repo = self._kv_repo
        if repo is None:
            repo = getattr(self._manager, "_shared_meta_kv_repo", None)
        if repo is None:
            return
        try:
            repo.set_many(
                self._kv_context_key, {ROOM_GC_KV_DRY_RUN_KEY: True}
            )
        except Exception as exc:  # noqa: BLE001 — the flag is advisory
            self._log.warning(
                "room gc: the durable dry-run flag write failed (%r) — "
                "this process will purge on the next tick (the in-RAM "
                "flag holds); a RESTART re-arms the dry run (fail-safe)",
                exc,
            )

    # ── the maintenance job entry point ───────────────────────────────

    async def execute(self) -> None:
        """The registered maintenance job — NEVER raises.

        The first sweep of a deployment is a DRY RUN (§2.2 "dry-run
        first"); every later sweep purges.

        The dry run is only COMPLETED (and the latch set) when the
        flag read was AUTHORITATIVE — the store answered it. A
        faulted read leaves the latch unset, so the next sweep re-reads
        the flag and re-decides: a KV fault can never convert a dry
        run into a purge (A-P6-6's fail-safe).
        """
        try:
            dry_run = not self._load_dry_run_flag()
            summary = await self.sweep(dry_run=dry_run)
            if dry_run:
                if self._dry_run_read_authoritative:
                    self._mark_dry_run_done()
                    self._log.info(
                        "room gc: DRY RUN complete — %d candidate(s), "
                        "%d purged (dry run purges nothing); the next sweep "
                        "executes the purge",
                        len(summary.get("candidates", [])),
                        len(summary.get("purged", [])),
                    )
                else:
                    # NOT authoritative — the dry run happened, but the
                    # flag the store never answered must NOT be latched:
                    # the next sweep re-reads and re-decides (fail-safe).
                    self._log.warning(
                        "room gc: DRY RUN complete — %d candidate(s), %d "
                        "purged (dry run purges nothing); the dry-run "
                        "latch was NOT set (the flag read was not "
                        "authoritative) — the next sweep RE-READS the "
                        "flag and re-decides (a fault never converts a "
                        "dry run into a purge)",
                        len(summary.get("candidates", [])),
                        len(summary.get("purged", [])),
                    )
            else:
                self._log.info(
                    "room gc: sweep complete — %d candidate(s), %d purged, "
                    "%d skipped",
                    len(summary.get("candidates", [])),
                    len(summary.get("purged", [])),
                    len(summary.get("skipped", [])),
                )
        except Exception as exc:  # noqa: BLE001 — a GC fault never kills the loop
            self._log.warning("room gc: sweep failed (%r)", exc)

    # ── the sweep ─────────────────────────────────────────────────────

    async def sweep(self, *, dry_run: bool = False) -> dict[str, Any]:
        """One GC pass. Returns a summary (also the test surface).

        The pass is: scan → decide → purge. The DECISION is computed
        for every room independently, so one room's failure never stops
        the others.
        """
        service = self._room_service()
        if service is None:
            return {
                "dry_run": dry_run,
                "candidates": [],
                "purged": [],
                "skipped": [],
                "errors": [],
                "unavailable": True,
            }

        now = datetime.now(timezone.utc)
        close_cutoff = now - timedelta(days=ROOM_GC_CLOSE_AGE_DAYS)
        idle_cutoff = now - timedelta(hours=ROOM_GC_IDLE_AGE_HOURS)

        closed_rooms = await service.list_rooms_by_state(RoomState.CLOSED.value)
        active_rooms = await service.list_rooms_by_state(RoomState.ACTIVE.value)

        candidates: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        # ── P-close ───────────────────────────────────────────────────
        for room in closed_rooms:
            closed_at = _parse_iso(room.get("closed_at"))
            if closed_at is None or closed_at >= close_cutoff:
                continue
            candidates.append(
                {
                    "room_id": room["room_id"],
                    "trigger": "p_close",
                    "reason": (
                        f"closed since {room.get('closed_at')} "
                        f"(> {ROOM_GC_CLOSE_AGE_DAYS}d)"
                    ),
                }
            )

        # ── P-idle ────────────────────────────────────────────────────
        idle_candidates: list[dict[str, Any]] = []
        for room in active_rooms:
            activity = _parse_iso(room.get("last_post_at")) or _parse_iso(
                room.get("created_at")
            )
            if activity is None or activity >= idle_cutoff:
                continue
            idle_candidates.append(
                {
                    "room_id": room["room_id"],
                    "trigger": "p_idle",
                    "reason": (
                        f"no activity since {room.get('last_post_at') or room.get('created_at')} "
                        f"(> {ROOM_GC_IDLE_AGE_HOURS}h)"
                    ),
                }
            )

        if idle_candidates:
            room_ids = [c["room_id"] for c in idle_candidates]
            member_map = await service.list_active_member_instance_ids(room_ids)
            guarded = await service.rooms_with_undelivered_tagged(room_ids)
            alive_map = await self._liveness_map(
                service, idle_candidates, member_map, active_rooms
            )
            for candidate in idle_candidates:
                room_id = candidate["room_id"]
                if room_id in guarded:
                    skipped.append(
                        {
                            "room_id": room_id,
                            "trigger": "p_idle",
                            "reason": "undelivered-tagged-rows",
                        }
                    )
                    continue
                alive = alive_map.get(room_id, [])
                if alive:
                    skipped.append(
                        {
                            "room_id": room_id,
                            "trigger": "p_idle",
                            "reason": f"live-participants:{','.join(alive)}",
                        }
                    )
                    continue
                candidates.append(candidate)

        # ── purge ─────────────────────────────────────────────────────
        purged: list[str] = []
        if dry_run:
            for candidate in candidates:
                self._log.info(
                    "room gc (dry run): would purge room=%s trigger=%s (%s)",
                    candidate["room_id"],
                    candidate["trigger"],
                    candidate["reason"],
                )
        else:
            for candidate in candidates:
                room_id = candidate["room_id"]
                try:
                    await service.purge_room(
                        room_id, reason=candidate["trigger"]
                    )
                except Exception as exc:  # noqa: BLE001 — one room never blocks the rest
                    errors.append(
                        {
                            "room_id": room_id,
                            "trigger": candidate["trigger"],
                            "error": repr(exc),
                        }
                    )
                    self._log.warning(
                        "room gc: purge failed for room=%s (%r)",
                        room_id,
                        exc,
                    )
                    continue
                purged.append(room_id)

        return {
            "dry_run": dry_run,
            "candidates": candidates,
            "purged": purged,
            "skipped": skipped,
            "errors": errors,
            "unavailable": False,
        }

    # ── liveness (the P-idle participant predicate) ───────────────────

    async def _liveness_map(
        self,
        service: RoomChatService,
        candidates: list[dict[str, Any]],
        member_map: dict[str, list[str]],
        room_rows: list[dict[str, Any]],
    ) -> dict[str, list[str]]:
        """``{room_id: [alive instance ids, ...]}`` for the P-idle set.

        The participant set is ``members(state=active) ∪ owner ∪
        owner-ancestry chain``. An instance counts as ALIVE iff its
        row exists AND its status is non-terminal (``waiting`` IS
        alive). A MISSING row is not alive (the row is gone — the walk
        stops there).

        The ancestor chain is walked LEVEL BY LEVEL (one batched query
        per level), bounded by ``_ANCESTRY_WALK_DEPTH`` — a parent
        cycle stops the walk (fail-closed: the chain is simply
        shorter).
        """
        wanted = {c["room_id"] for c in candidates}
        owners: dict[str, str] = {
            r["room_id"]: r["owner_instance_id"]
            for r in room_rows
            if r["room_id"] in wanted
        }

        # The owner-ancestry chain, per owner (batched per level).
        chains: dict[str, set[str]] = {
            owner: {owner} for owner in owners.values()
        }
        frontier: dict[str, str] = {
            owner: owner for owner in owners.values()
        }
        for _ in range(_ANCESTRY_WALK_DEPTH):
            nodes = {node for node in frontier.values() if node}
            if not nodes:
                break
            records = await service.get_instance_records(sorted(nodes))
            next_frontier: dict[str, str] = {}
            for owner, node in frontier.items():
                if not node:
                    continue
                record = records.get(node)
                if record is None:
                    continue
                parent_id = record.get("parent_id")
                if not parent_id or parent_id in chains[owner]:
                    continue
                chains[owner].add(parent_id)
                next_frontier[owner] = parent_id
            frontier = next_frontier

        alive: dict[str, list[str]] = {}
        for room_id in wanted:
            owner = owners.get(room_id)
            scope = set(member_map.get(room_id, []))
            if owner:
                scope.add(owner)
            scope |= chains.get(owner, set()) if owner else set()
            records = await service.get_instance_records(sorted(scope))
            alive[room_id] = sorted(
                instance_id
                for instance_id, record in records.items()
                if record.get("status")
                in NON_TERMINAL_INSTANCE_STATUSES_FOR_ROOMS
            )
        return alive

    # ── helpers ──────────────────────────────────────────────────────

    def _room_service(self) -> RoomChatService | None:
        """The room chat service handle (None ⇒ the sweeper no-ops)."""
        service = getattr(self._manager, "_room_chat_service", None)
        if service is None:
            return None
        return service
