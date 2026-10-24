"""DF-4 — the ARI-FALLBACK arm's behavioural pin (reviewer's open tester ticket).

Commission DF-4 (AGENT-ORIGIN provenance closure), verification lane W-D.

THE TICKET
----------
The shipped real-chain pin (``test_df4_provenance_closure.py``, pin (iii))
exercises ONLY the DIRECT-delivery arm — the sweep fallback literal site at
``upgrade_journal_sweep.py:1748`` inside ``sweep_wake_records``. The
ARI-FALLBACK arm — ``_deliver_wake_ari_fallback`` (``:1112``; its literal
site is ``:1217``; entered from the sweep's ``except KeyError`` at
``:1814``-``:1819``) — delivers to the project's FRONT-DOOR ``ari`` (the
most privileged instance) and was covered only TEXTUALLY by pin (v)'s
``count == 2`` source scan. This file is the behavioural proof.

WHAT THIS FILE PROVES (all through the REAL chain)
--------------------------------------------------
* The ari-fallback arm delivers the RESERVED literal
  (``SWEEP_NO_USER_ORIGIN_WAKE_SOURCE``, the SAME constant as the direct
  arm — the ternary at ``:1214``-``:1217`` is arm-exclusive) — and never
  ``"api"`` — read back from the REAL ``message_queue`` row for the ari
  target.
* The arm-exclusivity side of the selection: ``_resolve_fallback_target``
  (``:987``-``:1110``) runs REAL repository code — ``get_by_agent_id("ari")``
  (:470), the non-terminal filter (``:1048``-``:1054``), and the
  most-recent-activity + max-iid tie-break (``:1078``-``:1101``). The pin
  seeds ari rows that make each rule observable (a higher-iid stale row
  that must LOSE, a terminal row with the most recent activity that must
  be SKIPPED).
* The delivered wake takes the ``is_system_origin`` lane: the stamp shape
  (``_stamped_additional_kwargs`` — pin (vi)'s helper, instance_messaging.py
  ``:399``-``:417``) applied to the source ACTUALLY DELIVERED to the ari
  target is the injected shape, not a bare HumanMessage.
* NO user-origin window is minted on the ari target: the REAL manager's
  ``_user_origin_windows`` / ``_user_origin_last_stamp`` /
  ``_inherited_provenance_windows`` stay empty for it; the REAL dispatch
  funnel (``manager._process_message_with_tracking``, manager.py
  ``:9613``-``:9623``) takes the POP branch (``stamped is False``,
  ``detail == "reserved-internal"``); a delegated child of the ari target
  inherits NOTHING.
* NEGATIVE CONTROL (the shipped pin's honest-control pattern): a wake
  armed with a GENUINE ``"api"`` source rides the fallback VERBATIM (the
  documented contract at ``:1212``-``:1213`` — "a non-empty source still
  rides VERBATIM"), classifies ``ok is True``, and the funnel MINTS the
  window on the ari target — the disclosed REST residual shape (architect
  Q5). This proves the pin's differential is the SOURCE VALUE, not the
  plumbing.

THE ENTRY SEAM — READ THIS BEFORE JUDGING THE HARNESS
-----------------------------------------------------
The ari-fallback arm's ONLY entry is ``except KeyError`` wrapped around the
direct-arm ``enqueue_message`` (``:1693``-``:1819``). The contract that
seam encodes is documented in ``_resolve_fallback_target``'s docstring
(``:1006``-``:1017``): a gone target row makes the enqueue raise
``KeyError`` (per ``instance_lifecycle.get_instance`` — the REAL
``raise KeyError(f"Instance not found: ...")`` at
``instance_lifecycle.py:4511``).

PROBE FINDING (2026-10-22, this verification lane): the CURRENT
``InstanceMessagingService`` enqueue path no longer honours that contract
for a missing row — ``_prepare_enqueued_message`` logs a warning
(``instance_messaging.py:2458``-``:2461``) and writes the ``MessageQueue``
row UNCONDITIONALLY, so the missing-arming-row shape is ABSORBED by the
direct arm (the row lands on the ghost instance id; ``delivered == 1``;
the fallback never fires). The arm's KeyError entry therefore cannot be
reached by the ticket's stated construction on the current code.

Accordingly the entry seam below replays the DOCUMENTED KeyError contract
— for the GONE arming instance id ONLY — and delegates every other call
(INCLUDING the ari-fallback delivery itself) to the REAL bound
``manager.enqueue_message``. This is a delivery-TRANSPORT failure
injection at the exact seam the sweep documents. It is NOT a mock in the
provenance seam: ``stamp_user_origin_window`` /
``_inherit_provenance_window`` / the window stores / the classifier / the
target resolution / the delivered queue row are all REAL manager +
repository code over REAL PostgreSQL, mirroring the shipped pin (iii)
harness (no ``MagicMock`` manager, no ``FakeManager``, no hand-written
window dicts).

ENV FENCING
-----------
Same rails as the shipped pin (whose fence this file reuses): the
effective ``PG_TEST_DB`` must be neither a live/dev/prod database nor a
non-scratch-shaped name — checked at COLLECTION time (module import,
BEFORE the session-scoped ``pg_engine`` fixture's drop_all/create_all)
and AGAIN inside each test against the RESOLVED manager engine. The runs
of this verification lane used the PRIVATE scratch database
``df4v_ari_scratch`` — never ``ensemble_test`` (concurrent lanes +
conftest drop_all race), never any live/dev/prod database.

MANDATORY INVOCATION (a deselected/skipped pin is a FAILED gate)::

    pytest tests/postgres/test_df4_ari_fallback_closure.py \\
        --override-ini="addopts=" -m postgres
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generator

import pytest
from sqlmodel import Session, select

from daemon.manager import InstanceManager
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.message_queue.models import MessageQueue
from daemon.services.instance_messaging import _stamped_additional_kwargs
from daemon.services.upgrade_journal_sweep import UpgradeJournalSweepService
from daemon.tools import upgrade_journal as uj
from daemon.tools.upgrade_journal import (
    SWEEP_NO_USER_ORIGIN_WAKE_SOURCE,
    classify_user_origin,
)
from tests.helpers.df4_wake_fixtures import (
    append_journal_history,
    make_install,
    make_pending_wake,
)
from tests.postgres.test_df4_provenance_closure import (
    _assert_scratch_db,
    _pg_settings,
    install,  # noqa: F401 — the tmp-path journal fixture, reused (no copy-drift)
    real_manager,  # noqa: F401 — the REAL manager-over-pg_engine fixture, reused (no copy-drift)
)

pytestmark = pytest.mark.postgres


# ── the fence (collection-time; mirrors the shipped pin's rail) ────────────

try:
    _assert_scratch_db(
        _pg_settings()["db"], where="PG_TEST_DB (collection-time fence, ari pin)"
    )
except AssertionError as exc:
    raise RuntimeError(
        f"refused: {exc} — the DF-4 ari-fallback pin is scratch-only "
        "(point PG_TEST_DB at a scratch-shaped database)"
    ) from exc


# ── the actors ──────────────────────────────────────────────────────────────

#: The arming instance whose row does NOT exist — the gone-mid-tick shape.
GONE_ARM_IID = "i-df4v-ari-gone-arm"

#: ari candidate with RECENT activity but the lexicographically LOWER iid —
#: must WIN (most-recent-activity dominates max-iid).
ARI_CHOSEN_IID = "i-df4v-ari-a"

#: ari candidate with STALE activity and the lexicographically HIGHER iid —
#: must LOSE (proves the tie-break did not dominate the activity rule).
ARI_STALE_IID = "i-df4v-ari-z"

#: ari candidate with the MOST RECENT activity of all — but TERMINAL, so
#: the non-terminal filter (sweep :1048-:1054) must SKIP it.
ARI_DEAD_IID = "i-df4v-ari-dead"

#: A delegated child of the chosen ari target (the inherit leg).
CHILD_IID = "i-df4v-ari-child"


def _naive_utc(minutes_ago: float) -> datetime:
    """Naive-UTC datetime (the ``last_activity_at`` column is naive — DC-A)."""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).replace(
        tzinfo=None
    )


def _seed_ari_rows(manager: InstanceManager) -> None:
    """REAL rows: three ari candidates (one terminal) + the delegated child.

    The arming row (``GONE_ARM_IID``) is deliberately NEVER created.
    Activity instants are chosen so every selection rule is observable:
    ``ARI_DEAD_IID`` (terminal) has the most recent activity, and among
    the live rows the lexicographically HIGHER iid is the staler one.
    """
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with Session(manager.engine) as session:
        for iid in (ARI_CHOSEN_IID, ARI_STALE_IID, ARI_DEAD_IID, CHILD_IID):
            existing = session.exec(
                select(Instance).where(Instance.instance_id == iid)
            ).first()
            if existing is not None:
                session.delete(existing)

        def _ari(iid: str, status: str, activity: datetime) -> Instance:
            return Instance(
                instance_id=iid,
                agent_id="ari",
                agent_dir="agents/ari",
                status=status,
                instance_metadata={},
                project_id=None,
                created_at=old,
                updated_at=old,
                last_activity_at=activity,
            )

        # The terminal row is the most recently active — and MUST be skipped.
        session.add(
            _ari(ARI_DEAD_IID, InstanceStatus.TERMINATED.value, _naive_utc(1))
        )
        # The stale live row carries the HIGHER iid — and must lose.
        session.add(_ari(ARI_STALE_IID, InstanceStatus.IDLE.value, _naive_utc(30)))
        # The chosen live row: most-recent ACTIVITY among non-terminal rows.
        session.add(_ari(ARI_CHOSEN_IID, InstanceStatus.IDLE.value, _naive_utc(10)))
        # The delegated child of the chosen target (inherit leg).
        session.add(
            Instance(
                instance_id=CHILD_IID,
                agent_id="worker",
                agent_dir="agents/worker",
                parent_id=ARI_CHOSEN_IID,
                status=InstanceStatus.IDLE.value,
                instance_metadata={},
                created_at=old,
                updated_at=old,
            )
        )
        session.commit()


# ── the entry seam ──────────────────────────────────────────────────────────


def _arm_keyerror_entry_seam(manager: InstanceManager, gone_iid: str) -> None:
    """Replay the documented gone-row KeyError for the GONE arming id ONLY.

    See the module docstring ("THE ENTRY SEAM"): the sweep's
    ``except KeyError`` (``:1814``) encodes the contract that a gone
    target row makes the enqueue raise ``KeyError`` (the REAL
    ``Instance not found`` KeyError of
    ``daemon/services/instance_lifecycle.py:4511``). The current
    messaging path no longer raises for a missing row (probe finding),
    so this seam replays exactly that documented exception — same class,
    same message shape — for the gone arming id, and DELEGATES every
    other call to the REAL bound method (the ari-fallback delivery
    itself is a real enqueue).
    """
    real_enqueue = manager.enqueue_message

    async def gone_row_keyerror_seam(*, instance_id: str, **kwargs: Any):
        if instance_id == gone_iid:
            raise KeyError(f"Instance not found: {gone_iid}")
        return await real_enqueue(instance_id=instance_id, **kwargs)

    manager.enqueue_message = gone_row_keyerror_seam  # type: ignore[method-assign]


def _disarm_entry_seam(manager: InstanceManager) -> None:
    """Remove the instance-attribute shadow (un-delegate the seam)."""
    manager.__dict__.pop("enqueue_message", None)


# ── read-back helpers ───────────────────────────────────────────────────────


def _queue_rows(manager: InstanceManager) -> list[dict[str, Any]]:
    """The REAL ``message_queue`` rows, as plain dicts for assertions."""
    with Session(manager.engine) as session:
        return [
            {
                "message_id": r.message_id,
                "instance_id": r.instance_id,
                "content": r.content,
                "source": r.source,
                "type": r.type,
                "priority": r.priority,
                "metadata": r.message_metadata,
            }
            for r in session.exec(select(MessageQueue)).all()
        ]


def _history_events(install: Path) -> list[dict[str, Any]]:
    return list((uj.journal_read(install).get("history") or []))


async def _drive_funnel(
    manager: InstanceManager,
    instance_id: str,
    message_source: str,
    message_id: str,
) -> str:
    """Drive the REAL dispatch funnel (the shipped pin's pattern).

    The funnel entry point is ``InstanceManager._process_message_with_tracking``
    (manager.py); its FIRST two actions are the provenance sites
    (``stamp_user_origin_window`` :9615 + ``_inherit_provenance_window``
    :9623). The turn itself may fail downstream in a headless harness
    (no graph / no checkpointer) — the failure is swallowed and its
    diagnostic returned, so the follow-on asserts catch a funnel failure
    BEFORE the stamp site (the pin cannot pass vacuously).
    """
    try:
        await manager._process_message_with_tracking(
            instance_id=instance_id,
            message="df4 ari-fallback closure probe",
            message_id=message_id,
            message_source=message_source,
        )
    except Exception as exc:  # noqa: BLE001 — the turn may fail; the stamp ran
        return f"{type(exc).__name__}: {exc}"
    return "no exception"


# ── the pin ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sweep_ari_fallback_delivers_reserved_source_no_window_real_chain(
    real_manager, install
) -> None:
    """The ari-fallback arm delivers the RESERVED literal to the front-door
    ari, the delivered wake rides the injected lane, and NO user-origin
    window is minted on the ari target (nor inherited by its child).
    """
    manager = real_manager

    # The fixture's own fence, re-asserted against the RESOLVED engine
    # (mirrors the shipped pin :252-:257).
    settings = _pg_settings()
    resolved = manager.engine.url.database
    _assert_scratch_db(resolved, where="the RESOLVED manager engine database")
    assert resolved == settings["db"], (
        "the manager's engine must resolve to the same scratch database "
        f"the pg_engine fixture owns ({settings['db']!r} != {resolved!r})"
    )

    _seed_ari_rows(manager)

    # 1. a REAL wake armed with NO user origin; the arming row NEVER exists.
    make_pending_wake(
        install,
        source="",
        run_id="r-df4v-ari-fb",
        arming_instance_id=GONE_ARM_IID,
        message_id="m-arm-fb-1",
        message_metadata={},
    )
    append_journal_history(install, "commit", detail="df4v ari-fallback")

    # 2. the REAL sweep — the KeyError entry seam replays the documented
    #    gone-row contract (see module docstring); everything downstream
    #    (target resolution, delivery, journaling) is the REAL chain.
    _arm_keyerror_entry_seam(manager, GONE_ARM_IID)
    try:
        service = UpgradeJournalSweepService(install, manager=manager)
        result = await service.sweep_wake_records()
    finally:
        _disarm_entry_seam(manager)

    # 3. the DELIVERY arm ran — not the abandon arm. mark_wake_abandoned
    #    appends ``wake_abandoned`` + the fallback abandon writes
    #    ``arm_notify_no_instance`` (upgrade_journal.py:1397-:1405 /
    #    sweep :1150-:1176); mark_wake_delivered appends NOTHING and
    #    structurally removes the record (upgrade_journal.py:1348-:1384).
    events = _history_events(install)
    event_names = [e.get("event") for e in events]
    assert "arm_notify_no_instance" not in event_names, (
        f"the abandon arm fired, not the delivery arm: {event_names}"
    )
    assert not [e for e in events if e.get("event") == "wake_abandoned"], (
        f"the wake was abandoned, not delivered: {events}"
    )
    assert not (uj.journal_read(install).get("pending_wakes") or {}), (
        "the delivered wake must be structurally removed from pending_wakes"
    )
    # Counter corroboration ONLY — the queue row below is load-bearing
    # (the ari arm's counters are rebuilt/re-derived in both branches).
    assert result.abandoned == 0, f"diagnostic: {result}"
    assert result.delivered == 1, f"diagnostic: {result}"

    # 4. THE delivered row — read from the REAL message_queue.
    rows = _queue_rows(manager)
    assert len(rows) == 1, (
        f"exactly one delivery expected, got {len(rows)}: "
        f"{[(r['instance_id'], r['source']) for r in rows]}"
    )
    row = rows[0]
    # 4a. TARGET SELECTION (real repo code): the chosen live ari — NOT the
    #     terminal row (non-terminal filter), NOT the higher-iid stale row
    #     (activity dominates the max-iid tie-break), NOT the ghost arming
    #     id (the direct arm raised instead of delivering to it).
    assert row["instance_id"] == ARI_CHOSEN_IID, (
        f"the fallback must pick the most-recently-active non-terminal ari "
        f"({ARI_CHOSEN_IID!r}); got {row['instance_id']!r}"
    )
    # 4b. THE LITERAL — the whole point of the ticket: the reserved
    #     constant, never the bare user-origin literal.
    assert row["source"] == SWEEP_NO_USER_ORIGIN_WAKE_SOURCE, (
        f"the ari-fallback delivery must carry the reserved literal "
        f"{SWEEP_NO_USER_ORIGIN_WAKE_SOURCE!r}, got {row['source']!r}"
    )
    assert row["source"] != "api"
    # 4c. the fallback arm's production fingerprints — annotated body +
    #     fallback-kind system_context (sweep :1180-:1202).
    assert row["content"].startswith("(arm-notice:"), (
        "the fallback arm annotates the body; a direct-arm row would not"
    )
    system_context = (row["metadata"] or {}).get("system_context") or {}
    assert system_context.get("kind") == "post_restart_arm_notify_ari_fallback"
    assert (row["metadata"] or {}).get("delivery") == {
        "channel": "post_restart_arm_notify",
        "fallback": "ari",
    }

    # 5. the classifier fails the DELIVERED source CLOSED.
    ok, detail = classify_user_origin(row["source"], None)
    assert ok is False
    assert detail == "reserved-internal"

    # 6. the sweep's RE-STAMP did not fire (the loop is ``if w.source:``-
    #    gated at sweep :1707-:1717 and the armed source is empty) — the
    #    REAL manager's observation store for the TARGET must be untouched.
    assert manager._user_origin_last_stamp.get(ARI_CHOSEN_IID) is None, (
        "the sweep re-stamp must not fire for a no-user-origin wake "
        f"(observed: {manager._user_origin_last_stamp.get(ARI_CHOSEN_IID)!r})"
    )

    # 7. the is_system_origin lane — pin (vi)'s stamp helper applied to
    #    the source ACTUALLY DELIVERED: the wake turn is the injected
    #    shape, not a bare HumanMessage.
    stamped = _stamped_additional_kwargs(row["source"])
    assert stamped == {
        "injected_message": True,
        "source": row["source"],
    }

    # 8. the REAL funnel on the ari target takes the POP branch — no
    #    DIRECT window may be minted on the most privileged instance.
    funnel_diag = await _drive_funnel(
        manager, ARI_CHOSEN_IID, row["source"], "m-df4v-ari-funnel-1"
    )
    stamp = manager._user_origin_last_stamp[ARI_CHOSEN_IID]
    assert stamp["stamped"] is False, f"funnel diagnostic: {funnel_diag}"
    assert stamp["detail"] == "reserved-internal", (
        f"funnel diagnostic: {funnel_diag}"
    )
    assert ARI_CHOSEN_IID not in manager._user_origin_windows, (
        "no DIRECT window may be minted on the ari front-door target "
        f"(funnel diagnostic: {funnel_diag})"
    )
    assert ARI_CHOSEN_IID not in manager._inherited_provenance_windows

    # 9. the delegated child of the ari target inherits NOTHING — the
    #    forged-chain root mint would have propagated here.
    child_diag = await _drive_funnel(
        manager, CHILD_IID, f"internal_agent:{ARI_CHOSEN_IID}", "m-df4v-ari-funnel-2"
    )
    assert CHILD_IID not in manager._inherited_provenance_windows, (
        "no INHERITED window may be minted from the ari-fallback delivery "
        f"(funnel diagnostic: {child_diag})"
    )


@pytest.mark.asyncio
async def test_sweep_ari_fallback_genuine_source_rides_verbatim_real_chain(
    real_manager, install
) -> None:
    """NEGATIVE CONTROL — the pin above is not passing vacuously.

    The SAME real path with a GENUINE ``"api"``-sourced wake: the
    fallback rides the non-empty source VERBATIM (sweep :1212-:1213),
    the classifier arms, and the funnel MINTS the window on the ari
    target — the disclosed REST residual shape (architect Q5). The
    differential against the pin above is the SOURCE VALUE alone.
    """
    manager = real_manager
    _seed_ari_rows(manager)

    make_pending_wake(
        install,
        source="api",
        run_id="r-df4v-ari-genuine",
        arming_instance_id=GONE_ARM_IID,
        message_id="m-arm-genuine-1",
        message_metadata={},
    )
    append_journal_history(install, "commit", detail="df4v ari-fallback genuine")

    _arm_keyerror_entry_seam(manager, GONE_ARM_IID)
    try:
        service = UpgradeJournalSweepService(install, manager=manager)
        result = await service.sweep_wake_records()
    finally:
        _disarm_entry_seam(manager)

    events = _history_events(install)
    event_names = [e.get("event") for e in events]
    assert "arm_notify_no_instance" not in event_names
    assert result.abandoned == 0, f"diagnostic: {result}"
    assert result.delivered == 1, f"diagnostic: {result}"

    rows = _queue_rows(manager)
    assert len(rows) == 1
    row = rows[0]
    assert row["instance_id"] == ARI_CHOSEN_IID
    # The genuine source rides VERBATIM through the fallback arm.
    assert row["source"] == "api", f"got {row['source']!r}"
    ok, _detail = classify_user_origin(row["source"], None)
    assert ok is True

    # The REAL funnel MINTS the window on the ari target (the POP branch
    # does NOT run) — the exact shape the reserved literal prevents.
    funnel_diag = await _drive_funnel(
        manager, ARI_CHOSEN_IID, row["source"], "m-df4v-ari-genuine-1"
    )
    assert ARI_CHOSEN_IID in manager._user_origin_windows, (
        "a genuine user-origin source MUST mint the window on the ari "
        f"target (funnel diagnostic: {funnel_diag})"
    )
    stamp = manager._user_origin_last_stamp[ARI_CHOSEN_IID]
    assert stamp["stamped"] is True, f"funnel diagnostic: {funnel_diag}"
