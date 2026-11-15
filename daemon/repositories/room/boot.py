"""Room boot regeneration pass — PURE re-derivation (P1, pinned pair).

THE PINNED PAIR (architecture §10, D4; commission P1 scope item 4):

1. Boot regen re-derives TAGGED undelivered rows for MEMBERS —
   INCLUDING THE OWNER ROW — across rooms in ``active | closed``
   (purged rooms have no rows; there is no archived state), predicate
   ``seq > max(last_read_seq, last_delivered_seq)``.
2. Boot regen does NOT route, wake, or revive anything: this pass is
   pure re-derivation/preparation. Lane assignment (the total
   10-state map) is P3; parent-directed proposals and
   addressed-to-terminal notices stay durable pending rows under the
   one-hop rule and are surfaced only when the parent next runs
   through a legitimate lane.

F-1 blast radius (architecture §10 "By construction"): the F-1 boot
wipe's doomed predicate spans ``message_queue`` × ``task`` ONLY — the
room tables are a different table/PK space and are untouched. This
pass neither reads nor writes ``message_queue`` / ``task`` (the
F-1 pin test proves the wipe cannot reach room rows).

Best-effort by contract: :func:`run_room_boot_regen` returns a counts
dict and NEVER raises into the boot path — the caller (manager boot
hook) still wraps it in try/except per the house boot-pass shape
(maintenance_boot_sweep precedent).
"""

from __future__ import annotations

import logging
from typing import Any

from daemon.repositories.room.repository import RoomRepository

__all__ = ["run_room_boot_regen"]

logger = logging.getLogger(__name__)


def run_room_boot_regen(engine: Any) -> dict[str, Any]:
    """Re-derive the TAGGED undelivered set; log ONE line; return counts.

    Sync function — the manager boot hook calls it via
    ``asyncio.to_thread``. No instance row is read for routing, no
    message_queue/task row is touched, no status is flipped, nothing
    is enqueued or injected.
    """
    repo = RoomRepository(engine)
    pending = repo.derive_undelivered_tagged()
    rooms = sorted({row["room_id"] for row in pending})
    members = sorted({row["instance_id"] for row in pending})
    counts: dict[str, Any] = {
        "pending_rows": len(pending),
        "rooms_with_pending": len(rooms),
        "members_with_pending": len(members),
    }
    # ONE structured INFO line — the boot-time observability surface.
    # Content-free: counts + ids only (no message text ever).
    logger.info(
        "room boot regen (P1 re-derivation): pending_tagged=%d "
        "rooms=%d members=%d (no routing at P1 — durable pending rows "
        "surfaced by the parent's next legitimate lane per the "
        "one-hop rule)",
        counts["pending_rows"],
        counts["rooms_with_pending"],
        counts["members_with_pending"],
    )
    return counts
