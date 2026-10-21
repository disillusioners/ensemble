"""Boot-time recovery of ``message_queue`` rows stranded in 'processing'.

Feature: hard-reboot recovery. Phase 2 (G2).

A hard machine/VM reboot SIGKILLs the daemon mid-turn, leaving
``message_queue`` rows in ``status='processing'`` that no worker will
ever finish: the owning task is gone, so nothing advances them. Those
rows are counted by ``get_stats()`` and trip the ``send_message``
busy-guard (``daemon/tools/instance.py``), making the owning instance
look permanently busy to agent-to-agent sends.

This pass resets such rows to ``'ready'``. It is **reset-only**: it
never re-dispatches (a re-dispatch would mint a new visible turn and
could revive a terminal instance). Delivery stays task-driven — a row
with no live task rests at ``'ready'`` (the state of a freshly
enqueued, not-yet-claimed row); a row whose task is still pending is
picked up again by the normal claim path, which is the delivery the
reset unblocks.

Design notes:

* **Boot-epoch-relative, not age-relative.** The existing
  ``find_stuck_messages`` selector filters on ``last_activity_at`` age
  (``MESSAGE_TIMEOUT_SECONDS``). That axis is boot-INVALID: at boot
  every ``'processing'`` row predates the boot by construction, so the
  common hard-kill shape — a row claimed *seconds* before the power
  loss — is missed by any age threshold. The epoch is the correct axis
  ("was this row already in-flight when the previous process died?").
* **Skip-guards.** Rows whose instance is TERMINAL are skipped (revive
  hazard: a later revive would deliver a stale row) and rows backed by
  a RUNNING/PAUSED task are skipped (AutoContinue owns those and
  completes the message row itself).
* **Never wedges boot.** Three-layer isolation — per-row, per-pass and
  lifespan-level — mirroring
  ``daemon/services/auto_continue_boot_pass.py``.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

#: Operator kill-switch env var. Default ON; ``=0`` disables the pass.
#: Mirrors ``ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART``
#: (``daemon/services/auto_continue_boot_pass.py``). Read per boot (NOT
#: cached) so an operator flip takes effect on the next daemon restart.
HARD_REBOOT_BOOT_PASS_ENV = "ENSEMBLE_HARD_REBOOT_BOOT_PASS"


def recover_stuck_messages_on_startup(queue_repo, boot_epoch) -> int:
    """Reset stranded ``'processing'`` message rows to ``'ready'``.

    Reset-only (never re-dispatches). Idempotent across reboot-loops:
    the guarded per-row UPDATE only flips rows still in ``'processing'``,
    so a committed reset leaves the boot-epoch predicate.

    Synchronous by design — the queue repository is synchronous and
    this runs pre-worker-pool (nothing else contends for the event
    loop yet), mirroring the boot-epoch capture beside it.

    Args:
        queue_repo: The message-queue repository (see
            ``daemon/repositories/message_queue/repository.py``).
        boot_epoch: The process boot epoch (naive-UTC digits — see
            ``daemon/services/boot_epoch.py``). ``None`` means the
            capture failed, so there is no valid axis to select on and
            the pass is skipped.

    Returns:
        int: Number of rows reset.
    """
    if os.environ.get(HARD_REBOOT_BOOT_PASS_ENV, "1") == "0":
        logger.info("[HardReboot] MQ boot pass off (kill-switch %s=0)",
                    HARD_REBOOT_BOOT_PASS_ENV)
        return 0
    if boot_epoch is None:
        logger.warning("[HardReboot] MQ boot pass skipped: no boot epoch")
        return 0

    reset = 0
    try:
        for row in queue_repo.find_stuck_messages_before(boot_epoch):
            try:  # per-row isolation (layer 1) — one bad row must not
                # stop the sweep.
                if queue_repo.reset_stuck_message_to_ready(row.message_id):
                    reset += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("[HardReboot] MQ reset failed for %s: %s",
                               row.message_id, exc)
    except Exception as exc:  # noqa: BLE001
        # Per-pass isolation (layer 2) — never raise out of the pass.
        logger.warning("[HardReboot] MQ boot pass failed: %s", exc)
    logger.info("[HardReboot] MQ boot pass: reset=%d", reset)
    return reset
