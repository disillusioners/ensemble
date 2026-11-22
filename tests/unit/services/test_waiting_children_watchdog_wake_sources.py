"""Behavioral pin pack — WC-watchdog wake-source false-positive gate.

This pack pins the CONTRACT of the sibling fix (mod-wake-source-gate):

``WaitingChildrenWatchdog`` gains a tri-state
``_has_legitimate_wake_source(parent_id)`` consulted in the wedge pass
(after in-scope, before the carrier check, BOTH arms).  A parked WC
parent that still has a legitimate wake source must NOT receive a
``system:watchdog:wedge`` notice:

  (1) any live PENDING/RUNNING task row of ANY kind
      (``list_live_delivery_tasks_for_instance``);
  (2) armed job-watch rows (``watcher_repository.get_watches_for_instance``)
      whose watched receipt is NON-terminal (a stranded watch on a
      TERMINAL receipt is NOT a wake source);
  (3) an open PENDING/DEFERRED report_injection obligation
      (``manager._report_injection_repo``);
  (4) mission-live guard ``evaluate_mission_live(skip_root=True,
      bus_pending_count=fulfillable-only)`` — live descendants hold;
  (5) an outstanding mission_commissions row on any permanent-tree
      worker (``manager._mission_record_service``).

Tri-state: True ⇒ suppress + pop the wedge episode; None (a wired read
RAISED) ⇒ suppress WITHOUT popping; False ⇒ notice path proceeds; an
UNWIRED repo ⇒ that leg is False (existing unwired fixtures unchanged).

PINS ARE BEHAVIORAL: every assertion is on ``manager.enqueue_message``
notice calls.  Private helpers are never asserted on.  Seeding helpers
may monkeypatch an external dependency's *response*, but the verdict is
always read from the enqueue wake path.

BASE EVIDENCE (pre-fix, 8a1045c02): the FP pins (event-watch, parked
cascade) REPRODUCE the defect — the wedge notice FIRES on the base —
which is the RED/before evidence (live reference: anomaly a370b036,
four false notices on root 77ddcb9f 2026-11-21→22).  The true-wedge and
preservation pins are GREEN on base.  FP pins flip GREEN at integration
when the gate lands.

A compatibility constructor tolerates the pre-fix ctor (which lacks
``watcher_repository=``) so the SAME pack runs on base and on the fixed
branch without edits.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.services.waiting_children_watchdog import WaitingChildrenWatchdog

WEDGE_PREFIX = "system:watchdog"


# ─── Core fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine=engine)


@pytest.fixture
def manager() -> AsyncMock:
    """AsyncMock manager recording ``enqueue_message``.

    CRITICAL (house rule): AsyncMock auto-creates EVERY attribute as a
    truthy callable.  The gate consults optional manager surfaces
    (``_report_injection_repo``, ``_mission_record_service``,
    ``_queue_repository``, ``_task_repo``, ``_settings_service``).  We pin
    them to None so an UNWIRED leg is genuinely unwired — otherwise the
    gate would read a truthy MagicMock and suppress every pin for the
    WRONG reason (vacuous pass).
    """
    m = AsyncMock()
    m.enqueue_message = AsyncMock()
    for attr in (
        "_report_injection_repo",
        "_mission_record_service",
        "_queue_repository",
        "_task_repo",
        "_settings_service",
    ):
        setattr(m, attr, None)
    return m


@pytest.fixture
def make_instance(repo):
    """Insert an Instance row directly; return the instance_id."""
    counter = {"n": 0}

    def _factory(
        *,
        status: InstanceStatus = InstanceStatus.RUNNING,
        parent_id: str | None = None,
        age_seconds: int | None = 0,
        instance_id: str | None = None,
        instance_metadata: dict | None = None,
    ) -> str:
        counter["n"] += 1
        if instance_id is None:
            instance_id = f"i-{counter['n']:04d}"
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        last_activity = (
            (now - timedelta(seconds=age_seconds)).replace(tzinfo=None)
            if age_seconds is not None
            else None
        )
        with repo.engine.begin() as conn:
            conn.execute(
                Instance.__table__.insert().values(
                    instance_id=instance_id,
                    agent_id="test-agent",
                    agent_dir="/tmp/test-agent",
                    agent_name="test",
                    parent_id=parent_id,
                    status=status.value,
                    last_activity_at=last_activity,
                    metadata=instance_metadata or {},
                    created_at=now.replace(tzinfo=None).isoformat(),
                    updated_at=now.replace(tzinfo=None).isoformat(),
                )
            )
        return instance_id

    return _factory


@pytest.fixture
def task_repo():
    """A task repo with NO live carriers / delivery tasks / receipts.

    Methods the gate may call are all explicitly configured to EMPTY so
    an unwired leg is False.  ``MagicMock`` auto-attrs are never trusted.
    """
    t = MagicMock()
    t.list_live_process_report_carriers_for_instance = lambda iid: []
    t.list_live_delivery_tasks_for_instance = lambda iid: []
    t.get_by_work_id = lambda wid: None
    return t


@pytest.fixture
def watcher_repo():
    wr = MagicMock()
    wr.get_watches_for_instance = lambda iid: []
    return wr


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _make_watchdog(repo, manager, *, task_repo, watcher_repo=None, **kw):
    """Construct a watchdog against BOTH base and fixed ctors.

    Base (8a1045c02) has no ``watcher_repository=`` arg; the fixed branch
    does.  On base we attach the repo as a plain attribute (harmless —
    the gate does not exist there).  The pack never asserts on the gate
    helper, so the construction path is behaviorally irrelevant.
    """
    base = dict(
        interval_seconds=1,
        hang_threshold_seconds=1,
        task_repository=task_repo,
        **kw,
    )
    params = inspect.signature(WaitingChildrenWatchdog.__init__).parameters
    if "watcher_repository" in params:
        return WaitingChildrenWatchdog(
            repo, manager, watcher_repository=watcher_repo, **base
        )
    w = WaitingChildrenWatchdog(repo, manager, **base)
    w._watcher_repository = watcher_repo
    return w


def _wedge_calls(manager) -> list:
    """All enqueue calls that are watchdog notices (behavioral read)."""
    return [
        c
        for c in manager.enqueue_message.call_args_list
        if str(c.kwargs.get("source", "")).startswith(WEDGE_PREFIX)
    ]


def _assert_fires(manager, why: str = "") -> None:
    assert len(_wedge_calls(manager)) >= 1, (
        "expected a wedge notice on the wake path" + (f" — {why}" if why else "")
    )


def _assert_silent(manager, why: str = "") -> None:
    assert not _wedge_calls(manager), (
        "expected NO wedge notice on the wake path" + (f" — {why}" if why else "")
    )


def _assert_silent_for(manager, instance_id: str, why: str = "") -> None:
    """Assert no wedge notice targeted at ONE parent.

    Used where another (genuinely wedged) WC instance in the same fixture
    legitimately gets its own notice — e.g. the parked-cascade grandchild.
    """
    offending = [
        c for c in _wedge_calls(manager)
        if c.kwargs.get("instance_id") == instance_id
    ]
    assert not offending, (
        f"expected NO wedge notice for {instance_id}" + (f" — {why}" if why else "")
    )


def _wedged_parent(make_instance, *, pid="wc-root") -> str:
    """The classic wedged shape: WC parent + one terminal direct child."""
    parent = make_instance(status=InstanceStatus.WAITING_CHILDREN, instance_id=pid)
    make_instance(status=InstanceStatus.COMPLETED, parent_id=parent)
    return parent


def _subject_row(child_id: str = "terminal-child"):
    """A DependencyWatcher-shaped unfulfillable subject row (fake bus)."""
    return SimpleNamespace(
        watch_id="w-subject-1",
        source_task_id="task-x",
        target_instance_id=child_id,
        state="PENDING",
        follow_up_payload={"metadata": {"child_id": child_id}},
        watcher_metadata={},
    )


def _install_fake_bus(monkeypatch, subjects_map: dict[str, tuple[int, list]]):
    """Seed the S4 bus read (total_pending, unfulfillable_rows) per parent."""

    def _fetch(ids):
        wanted = set(ids)
        return {k: v for k, v in subjects_map.items() if k in wanted}

    fake_bus = MagicMock()
    fake_bus.fetch_pending_subject_for_targets_sync = _fetch
    import daemon.services.dependency_bus as db_mod

    monkeypatch.setattr(db_mod, "get_dependency_bus", lambda: fake_bus)
    return fake_bus


def _arm_live_task(task_repo, parent_id: str):
    """LEG 1 — a live PENDING/RUNNING delivery task for the parent."""
    from daemon.repositories.task.models import Task, TaskStatus

    task = Task(
        work_id="live-delivery-wid",
        instance_id=parent_id,
        task_type="process_message",
        status=TaskStatus.RUNNING.value,
    )
    task_repo.list_live_delivery_tasks_for_instance = lambda iid: (
        [task] if iid == parent_id else []
    )
    return task


def _arm_event_watch(
    watcher_repo, task_repo, parent_id: str, *, receipt_terminal: bool = False
):
    """LEG 2 — an armed job-watch on a NON-terminal receipt (default).

    ``receipt_terminal=True`` seeds a STRANDED watch (terminal receipt),
    which must NOT count as a wake source (pin 7).
    """
    from daemon.repositories.job_queue.watcher_models import JobWatcher
    from daemon.repositories.task.models import Task, TaskStatus

    watch = JobWatcher(job_id="watched-receipt-wid", instance_id=parent_id)
    watcher_repo.get_watches_for_instance = lambda iid: (
        [watch] if iid == parent_id else []
    )
    receipt = Task(
        work_id="watched-receipt-wid",
        instance_id="receipt-worker",
        task_type="process_message",
        status=(
            TaskStatus.COMPLETED.value
            if receipt_terminal
            else TaskStatus.RUNNING.value
        ),
    )
    task_repo.get_by_work_id = lambda wid: (
        receipt if wid == "watched-receipt-wid" else None
    )
    return watch, receipt


def _arm_report_obligation(manager):
    """LEG 3 — an open report_injection obligation (mock repo response).

    The gate consults ``manager._report_injection_repo``; a positive
    count on ANY plausible read method means a wake source is present.
    """
    ri = MagicMock()
    ri.count_outstanding_for_subtree = lambda subtree_ids, **kw: 1
    ri.count_pending_for_parent = lambda pid: 1
    ri.find_deferred_for_parent = lambda pid: [SimpleNamespace(state="DEFERRED")]
    manager._report_injection_repo = ri
    return ri


def _arm_commission(manager):
    """LEG 5 — an outstanding commission on a permanent-tree worker."""
    mrs = MagicMock()
    cr = MagicMock()
    cr.find_outstanding_commission_for_worker = lambda wid, **kw: SimpleNamespace(
        commission_id="comm-1", state="outstanding"
    )
    cr.find_routing_commission_for_worker = lambda wid, **kw: SimpleNamespace(
        commission_id="comm-1", state="outstanding"
    )
    mrs.commission_repository = lambda: cr
    manager._mission_record_service = mrs
    return mrs


def _patch_mission_live(monkeypatch, fake):
    """Patch evaluate_mission_live at BOTH its source and any rebound alias.

    The fix may ``from ... import evaluate_mission_live`` (module-level
    alias) or call via module reference.  Patching both makes the seed
    binding-style independent.  ``fake`` must be async.
    """
    import daemon.services.mission_live_guard as mlg
    import daemon.services.waiting_children_watchdog as wcm

    original = mlg.evaluate_mission_live
    monkeypatch.setattr(mlg, "evaluate_mission_live", fake, raising=False)
    for name in dir(wcm):
        if getattr(wcm, name, None) is original:
            monkeypatch.setattr(wcm, name, fake, raising=False)


# ═════════════════════════════════════════════════════════════════════════════
# PIN 1 — FP shape (a): event-watch on a non-terminal receipt ⇒ NO notice
# ═════════════════════════════════════════════════════════════════════════════


class TestFPShapeAEventWatch:
    async def test_armed_watch_on_non_terminal_receipt_suppresses(
        self, repo, manager, make_instance, task_repo, watcher_repo
    ):
        parent = _wedged_parent(make_instance, pid="wc-event-watch")
        _arm_event_watch(watcher_repo, task_repo, parent, receipt_terminal=False)
        w = _make_watchdog(
            repo, manager, task_repo=task_repo, watcher_repo=watcher_repo
        )
        await w.run_once()
        _assert_silent(
            manager,
            "an armed watch on a non-terminal receipt is a legitimate wake "
            "source (FP shape a) — no wedge notice",
        )


# ═════════════════════════════════════════════════════════════════════════════
# PIN 2 — FP shape (b): parked cascade (live grandchild) ⇒ NO notice
# ═════════════════════════════════════════════════════════════════════════════


class TestFPShapeBParkedCascade:
    async def test_live_grandchild_holds_mission_live(
        self, repo, manager, make_instance, task_repo
    ):
        # Direct children all terminal, but a GRANDCHILD is parked WC —
        # a live descendant the mission-live guard (skip_root=True) sees.
        parent = make_instance(
            status=InstanceStatus.WAITING_CHILDREN, instance_id="wc-cascade"
        )
        child = make_instance(
            status=InstanceStatus.COMPLETED, parent_id=parent
        )
        make_instance(status=InstanceStatus.WAITING_CHILDREN, parent_id=child)
        w = _make_watchdog(repo, manager, task_repo=task_repo)
        await w.run_once()
        # The grandchild is itself a leaf WC instance and may legitimately
        # draw its own wedge notice; assert the PARENT (whose live
        # descendant holds mission-live) is the one kept silent.
        _assert_silent_for(
            manager,
            parent,
            "a live grandchild holds mission-live with skip_root=True — "
            "no wedge notice for the parked parent (FP shape b, parked cascade)",
        )


# ═════════════════════════════════════════════════════════════════════════════
# PIN 3 — TRUE WEDGE: all-terminal, no wake sources ⇒ notice STILL fires
# ═════════════════════════════════════════════════════════════════════════════


class TestTrueWedgeFires:
    async def test_classic_arm_all_terminal(
        self, repo, manager, make_instance, task_repo
    ):
        _wedged_parent(make_instance, pid="wc-true-classic")
        w = _make_watchdog(repo, manager, task_repo=task_repo)
        await w.run_once()
        _assert_fires(manager, "classic arm: no watchers/carrier/obligations")

    async def test_s4_unfulfillable_subject_arm(
        self, repo, manager, make_instance, task_repo, monkeypatch
    ):
        parent = _wedged_parent(make_instance, pid="wc-true-s4")
        # One unfulfillable bus watcher (its child terminal) → in scope via
        # the S4 subject arm, but NO legitimate wake source remains.
        _install_fake_bus(
            monkeypatch, {parent: (1, [_subject_row("terminal-child")])}
        )
        w = _make_watchdog(repo, manager, task_repo=task_repo)
        await w.run_once()
        _assert_fires(manager, "S4 arm: unfulfillable subject, no live wake source")


# ═════════════════════════════════════════════════════════════════════════════
# PIN 4 — fulfillable / unfulfillable split (mission-live bus_pending_count)
# ═════════════════════════════════════════════════════════════════════════════


class TestFulfillableSplit:
    async def test_unfulfillable_only_subject_fires(
        self, repo, manager, make_instance, task_repo, monkeypatch
    ):
        parent = _wedged_parent(make_instance, pid="wc-unful-only")
        # total_pending == unfulfillable count ⇒ fulfillable-only == 0 ⇒
        # mission-live bus_pending_count=0 ⇒ no live leg ⇒ notice fires.
        _install_fake_bus(
            monkeypatch, {parent: (1, [_subject_row("terminal-child")])}
        )
        w = _make_watchdog(repo, manager, task_repo=task_repo)
        await w.run_once()
        _assert_fires(manager, "unfulfillable-only subject ⇒ S4 fires")

    async def test_unfulfillable_plus_fulfillable_suppresses(
        self, repo, manager, make_instance, task_repo, monkeypatch
    ):
        parent = _wedged_parent(make_instance, pid="wc-unful-plus-ful")
        # total_pending=2, one unfulfillable ⇒ fulfillable-only == 1 ⇒
        # mission-live bus_pending_count=1 ⇒ live leg holds ⇒ suppress.
        # A LIVE direct child is also seeded so suppression is robust to
        # either the bus-count leg or the descendant-liveness leg.
        make_instance(status=InstanceStatus.RUNNING, parent_id=parent)
        _install_fake_bus(
            monkeypatch, {parent: (2, [_subject_row("terminal-child")])}
        )
        w = _make_watchdog(repo, manager, task_repo=task_repo)
        await w.run_once()
        _assert_silent(
            manager, "unfulfillable + fulfillable sibling ⇒ suppressed"
        )


# ═════════════════════════════════════════════════════════════════════════════
# PIN 5 — tri-state fail-closed: each leg wired-but-raising ⇒ no notice,
#         and the episode entry is NOT popped (behavioral, multi-tick)
# ═════════════════════════════════════════════════════════════════════════════


class TestTriStateFailClosed:
    async def _raising_leg_case(self, leg, repo, manager, make_instance,
                                task_repo, watcher_repo, monkeypatch):
        parent = _wedged_parent(make_instance, pid=f"wc-raise-{leg}")
        w = _make_watchdog(
            repo, manager, task_repo=task_repo, watcher_repo=watcher_repo
        )
        if leg == "live_task":
            task_repo.list_live_delivery_tasks_for_instance = MagicMock(
                side_effect=RuntimeError("leg1 down")
            )
        elif leg == "job_watch":
            watcher_repo.get_watches_for_instance = MagicMock(
                side_effect=RuntimeError("leg2 down")
            )
        elif leg == "report_obligation":
            ri = MagicMock()
            ri.count_outstanding_for_subtree = MagicMock(
                side_effect=RuntimeError("leg3 down")
            )
            manager._report_injection_repo = ri
        elif leg == "mission_live":

            async def _boom(**kwargs):
                raise RuntimeError("leg4 down")

            _patch_mission_live(monkeypatch, _boom)
        elif leg == "commission":
            mrs = MagicMock()
            cr = MagicMock()
            cr.find_outstanding_commission_for_worker = MagicMock(
                side_effect=RuntimeError("leg5 down")
            )
            mrs.commission_repository = lambda: cr
            manager._mission_record_service = mrs
        else:
            raise AssertionError(leg)
        await w.run_once()
        _assert_silent(manager, f"{leg} wired-but-raising ⇒ fail closed")

    async def test_leg1_live_task_raising(self, repo, manager, make_instance,
                                          task_repo, watcher_repo, monkeypatch):
        await self._raising_leg_case(
            "live_task", repo, manager, make_instance, task_repo, watcher_repo,
            monkeypatch,
        )

    async def test_leg2_job_watch_raising(self, repo, manager, make_instance,
                                          task_repo, watcher_repo, monkeypatch):
        await self._raising_leg_case(
            "job_watch", repo, manager, make_instance, task_repo, watcher_repo,
            monkeypatch,
        )

    async def test_leg3_report_obligation_raising(
        self, repo, manager, make_instance, task_repo, watcher_repo, monkeypatch
    ):
        await self._raising_leg_case(
            "report_obligation", repo, manager, make_instance, task_repo,
            watcher_repo, monkeypatch,
        )

    async def test_leg4_mission_live_raising(
        self, repo, manager, make_instance, task_repo, watcher_repo, monkeypatch
    ):
        await self._raising_leg_case(
            "mission_live", repo, manager, make_instance, task_repo,
            watcher_repo, monkeypatch,
        )

    async def test_leg5_commission_raising(
        self, repo, manager, make_instance, task_repo, watcher_repo, monkeypatch
    ):
        await self._raising_leg_case(
            "commission", repo, manager, make_instance, task_repo, watcher_repo,
            monkeypatch,
        )

    async def test_unknown_read_does_not_pop_episode(
        self, repo, manager, make_instance, task_repo, watcher_repo
    ):
        """None (a wired read raised) suppresses WITHOUT popping.

        Behavioral three-tick proof on the enqueue path:
          tick 1 — pure wedge ⇒ notice fires (episode stamped);
          tick 2 — leg raises ⇒ no notice, entry retained;
          tick 3 — pure wedge again ⇒ entry still present ⇒ dedupe keeps
                   it silent.  (If tick 2 had popped the entry, tick 3
                   would re-notify.)
        """
        parent = _wedged_parent(make_instance, pid="wc-unknown-nopop")
        w = _make_watchdog(
            repo, manager, task_repo=task_repo, watcher_repo=watcher_repo
        )

        await w.run_once()  # tick 1 — opens the episode
        _assert_fires(manager, "tick 1 must open the wedge episode")
        manager.enqueue_message.reset_mock()

        task_repo.list_live_delivery_tasks_for_instance = MagicMock(
            side_effect=RuntimeError("leg1 down")
        )
        await w.run_once()  # tick 2 — UNKNOWN
        _assert_silent(manager, "tick 2 (unknown) must not notify")

        # Restore the leg; fingerprint unchanged.
        task_repo.list_live_delivery_tasks_for_instance = lambda iid: []
        await w.run_once()  # tick 3 — dedupe must still hold
        _assert_silent(
            manager,
            "tick 3: an UNKNOWN read must NOT have popped the episode — "
            "the unchanged fingerprint still dedupes",
        )


# ═════════════════════════════════════════════════════════════════════════════
# PIN 6 — skip_root polarity: the parked parent's OWN WC status never holds
#         (mutation skip_root=True→False ⇒ this pin goes RED)
# ═════════════════════════════════════════════════════════════════════════════


class TestSkipRootPolarity:
    async def test_parent_own_wc_status_does_not_hold(
        self, repo, manager, make_instance, task_repo
    ):
        # WC parent, all children terminal, no descendants, no bus
        # watchers.  With skip_root=True the real mission-live guard
        # reports live=False (proved on base) ⇒ the wedge STILL fires.
        # Passing skip_root=False would make the parent's own non-terminal
        # status hold the guard ⇒ suppress ⇒ this pin RED.
        _wedged_parent(make_instance, pid="wc-skip-root")
        w = _make_watchdog(repo, manager, task_repo=task_repo)
        await w.run_once()
        _assert_fires(
            manager,
            "skip_root=True: the parked parent's own WC status is not a "
            "live descendant",
        )


# ═════════════════════════════════════════════════════════════════════════════
# PIN 7 — stranded watch: armed watch on a TERMINAL receipt does NOT suppress
# ═════════════════════════════════════════════════════════════════════════════


class TestStrandedWatch:
    async def test_terminal_receipt_watch_does_not_suppress(
        self, repo, manager, make_instance, task_repo, watcher_repo
    ):
        parent = _wedged_parent(make_instance, pid="wc-stranded-watch")
        _arm_event_watch(
            watcher_repo, task_repo, parent, receipt_terminal=True
        )
        w = _make_watchdog(
            repo, manager, task_repo=task_repo, watcher_repo=watcher_repo
        )
        await w.run_once()
        _assert_fires(
            manager,
            "a stranded watch on a TERMINAL receipt is NOT a wake source",
        )


# ═════════════════════════════════════════════════════════════════════════════
# PIN 8 — preservation: l6_anchor exemption, kill-switch emission-only,
#         fingerprint dedupe unchanged
# ═════════════════════════════════════════════════════════════════════════════


class TestPreservation:
    @pytest.fixture(autouse=True)
    def _reset_b_ledger(self):
        from daemon.services import report_integrity_guard as rig

        rig._B_NOTICE_LEDGER.clear()
        yield
        rig._B_NOTICE_LEDGER.clear()

    async def test_l6_anchor_exemption_still_active(
        self, repo, manager, make_instance, task_repo
    ):
        from daemon.services import report_integrity_guard as rig
        from daemon.services.attestation_ledger import L6_ANCHOR_META_KEY

        parent = make_instance(
            status=InstanceStatus.WAITING_CHILDREN,
            instance_id="wc-l6-anchor",
            instance_metadata={L6_ANCHOR_META_KEY: True},
        )
        make_instance(status=InstanceStatus.COMPLETED, parent_id=parent)
        # An open (b) notice would normally skip the wedge; the l6_anchor
        # exemption must keep the backstop alive.
        rig._B_NOTICE_LEDGER[parent] = "episode-1"
        w = _make_watchdog(repo, manager, task_repo=task_repo)
        await w.run_once()
        _assert_fires(manager, "l6_anchor exemption keeps the wedge live")

    async def test_kill_switch_is_emission_only(
        self, repo, manager, make_instance, task_repo, monkeypatch
    ):
        """OFF ⇒ no notice and NO episode stamped; back ON ⇒ notice fires."""
        parent = _wedged_parent(make_instance, pid="wc-killswitch")
        w = _make_watchdog(repo, manager, task_repo=task_repo)
        monkeypatch.setattr(
            w, "_wedge_notice_emission_enabled", lambda: False
        )
        await w.run_once()
        _assert_silent(manager, "kill-switch OFF ⇒ no emission")

        # Flip back ON on the SAME watchdog: no restart needed and no
        # episode was stamped while OFF, so the notice must now fire.
        monkeypatch.setattr(w, "_wedge_notice_emission_enabled", lambda: True)
        w._wedge_emission_cache = None
        await w.run_once()
        _assert_fires(manager, "kill-switch back ON ⇒ notice fires (no stamp while OFF)")

    async def test_fingerprint_dedupe_unchanged(
        self, repo, manager, make_instance, task_repo
    ):
        _wedged_parent(make_instance, pid="wc-dedupe")
        w = _make_watchdog(repo, manager, task_repo=task_repo)
        await w.run_once()
        await w.run_once()
        assert len(_wedge_calls(manager)) == 1, (
            "two ticks with the same fingerprint ⇒ exactly one notice"
        )
