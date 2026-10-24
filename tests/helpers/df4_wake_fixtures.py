"""Shared fixtures for the DF-4 wake-arming harness.

Commission DF-4 (AGENT-ORIGIN provenance gate closure) — a TIDIER-pass
extraction. Three test files duplicate the same install-dir setup, the
same ``PendingWake`` construction and the same journal-history append;
the byte-copied trios drift (the house pattern — ``tests/helpers/``
already holds 15 shared modules):

  * ``tests/unit/tools/test_df4_sweep_reserved_fallback.py`` — pin (v).
  * ``tests/postgres/test_df4_provenance_closure.py`` — pin (iii).
  * ``tests/job_queue/test_post_restart_arm_notify_routing.py`` — the
    pre-existing restart-arm routing pack.

The ``PendingWake`` FIELD SET is preserved EXACTLY. The three call
sites differ in three fields (``message_id``, ``message_metadata``,
``arming_instance_id``) and those three are PARAMETERISED — the shared
fields (``env``, ``arming_agent_id``, ``target_version``, ``mode``,
``armed_at``, ``expires_at``, ``abandon_after``, ``status``) keep every
call site's original spelling, including the
``iso_plus("2026-10-04T00:10:00Z", PENDING_WAKE_GRACE_S)`` abandon
window. The field sets are NOT unified — a call site that armed with
``message_metadata={}`` still does.

Importable from all three test trees via the ``tests.helpers`` package
— no ``sys.path`` manipulation needed (the house pattern used by
``tests.helpers.send_message_fixtures`` and
``tests.helpers.hard_reboot_fixtures``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from daemon.tools import upgrade_journal as uj
from daemon.tools.upgrade_journal import (
    PENDING_WAKE_GRACE_S,
    PendingWake,
    arm_pending_wake,
    iso_plus,
    journal_init,
)

#: The fixed armed/expires instants every call site armed against.
ARMED_AT = "2026-10-04T00:00:00Z"
EXPIRES_AT = "2026-10-04T00:10:00Z"


def make_install(tmp_path: Path) -> Path:
    """A tmp install dir with a real journal — never a live-shaped path."""
    inst = tmp_path / "install"
    (inst / "releases").mkdir(parents=True)
    journal_init(inst)
    uj.ensure_extensions(inst)
    return inst


def make_pending_wake(
    install: Path,
    *,
    source: str = "discord:user123",
    kind: str = "promote",
    run_id: str = "r-df4",
    arming_instance_id: str = "i-arm-1",
    message_id: str | None = "m-original-1",
    message_metadata: dict[str, Any] | None = None,
) -> PendingWake:
    """Arm a REAL ``PendingWake`` with the given source.

    The three differing fields are keyword-only parameters so a call
    site keeps its EXACT original field set; ``message_metadata``
    defaults to the house ``{"k": "v"}`` and an explicit ``{}`` is
    preserved as ``{}`` (the empty dict is NOT conflated with the
    default).

    ``kind`` defaults to ``"promote"`` (agent-restart: the ADR-041
    routing/re-stamp pins are the PROMOTE-lane contract — restart-kind
    wakes deliver reserved-sourced and never re-stamp, pinned in
    ``tests/unit/tools/test_agent_restart.py`` + the wake drills; the
    default keeps those routing pins testing what they were written to
    test).
    """
    if message_metadata is None:
        message_metadata = {"k": "v"}
    wake = PendingWake(
        run_id=run_id,
        kind=kind,
        env="demo",
        arming_instance_id=arming_instance_id,
        arming_agent_id="ari",
        source=source,
        message_id=message_id,
        message_metadata=message_metadata,
        target_version=None,
        mode="graceful-now",
        armed_at=ARMED_AT,
        expires_at=EXPIRES_AT,
        abandon_after=iso_plus(EXPIRES_AT, PENDING_WAKE_GRACE_S),
        status="pending",
    )
    arm_pending_wake(install, wake)
    return wake


def append_journal_history(
    install: Path,
    event: str,
    *,
    ts: str = "2026-10-04T00:01:00Z",
    detail: str = "...",
) -> None:
    """Append one history event to the install's journal."""
    data = uj.journal_read(install)
    history = data.get("history") or []
    history.append({"ts": ts, "event": event, "detail": detail})
    data["history"] = history
    uj.journal_write(install, data)
