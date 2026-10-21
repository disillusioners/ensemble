"""Watcher-loss terminal-strand — commission-mandated gap tests.

Pins the scenario-mapping gaps identified for the
``feature/watcher-loss-terminal-strand`` commission (base
27d2452a7..HEAD). The existing suite
``tests/job_queue/test_watcher_loss_terminal_strand.py`` covers
scenarios 1/2/4/5/7 with nuances; the commission adjudication found
these gaps:

* G1  — scenario-3 conjunct: denial → completion must compose. A
  denied spawn on the root's final turn must NOT prevent the root
  from reaching COMPLETED, the watcher from being CAS-claimed, and
  the lifecycle completed event from firing.
* G2a — scenario-6 healer lane: a raised ghost probe fails CLOSED.
  The healer must NOT terminalize the orphan nor drive the root.
* G2b — scenario-6 guard lane: a raised ghost probe in
  ``evaluate_mission_live`` must keep the mission held (fail-CLOSED
  on probe error — the strand exclusion does NOT fire when the probe
  cannot prove the ghost signature).
* G3  — scenario-6: a PAUSED straggler (not never-dispatched-shaped)
  under a TERMINAL parent must abort the heal (safety bound) — the
  healer must NOT terminalize it.
* G4  — scenario-9: a ghost whose DIRECT parent row is missing from
  the repository read (UNKNOWN parent status) must fail-closed in
  BOTH lanes — the guard keeps the mission held; the healer aborts.
* G5  — scenario-9: a root that is ALREADY-TERMINAL when the healer
  looks is a no-op (no terminate, no drive, no events).
* G6  — scenario-9: double-heal idempotency — once a heal succeeded
  in one sweep_once, a subsequent sweep_once is a no-op (scanned
  unchanged, no new events, root unchanged).
* G7  — zero-obligation leg (3): a PENDING task row for the root
  aborts the heal (the in-flight turn is a live carrier — the wedge
  resolves event-driven, not via the healer).

Harness shape mirrors ``test_watcher_loss_terminal_strand.py``:
real engine, real repositories, real lifecycle service
termination lane (H10). The probe-raise tests monkeypatch
``SQLModelInstanceRepository.filter_never_dispatched_ids`` so the
shape stays real-path; only the probe is forced to RAISE.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session

from daemon.constants import TERMINAL_INSTANCE_STATUSES
from daemon.repositories.event.models import Event, EventKind
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import (
    SQLModelInstanceRepository,
)
from daemon.repositories.job_queue.models import AdmissionState
from daemon.repositories.job_queue.repository import JobRepository
from daemon.repositories.job_queue.watcher_models import (
    ALL_WATCHABLE_EVENTS,
    JobWatcher,
)
from daemon.repositories.job_queue.watcher_repository import (
    JobWatcherRepository,
)
from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.task.models import Task as TaskModel
from daemon.services import mission_live_guard as _mlg
from daemon.services.child_reports import ChildReportsService
from daemon.services.job_queue_service import JobQueueService
from daemon.services.timestamps import now_utc_naive
from daemon.services.work_resolver import WorkResolverService

# Re-use the existing test module's helpers + constants. The harness
# itself is rebuilt locally (the fixture mechanism is module-local —
# pytest fixtures cannot be imported as callables, but the helper
# functions ARE plain functions, importable freely).
from tests.job_queue.test_watcher_loss_terminal_strand import (  # noqa: E402
    ROOT,
    TESTER,
    GHOST,
    RECEIPT_JOB,
    WATCHER_INSTANCE,
    T_CHILD_COMPLETED,
    T_FRESH_ASSISTANT,
    _seed_poison_tree,
    _insert_instance,
    _insert_receipt,
    _fresh_history,
    _seed_child_completed_event,
    _get_instance_row,
    _job_admission_state,
)


# ─────────────────────────────────────────────────────────────────────────────
# Engine + harness
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def gap_engine():
    """Per-test in-memory SQLite engine (mirror of ``strand_engine``)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


class _DrivePatchStack:
    """Reusable context-manager wrapper (contextlib.ExitStack based)
    so the gap tests can wrap the drive path in the same patches the
    existing suite uses (checkpoint history extractor + timestamp +
    bridge)."""

    def __init__(self, history: list[dict]):
        self._history = history

    def __enter__(self):
        from contextlib import ExitStack
        self._stack = ExitStack()
        self._stack.enter_context(patch(
            "daemon.services.child_reports.get_instance_messages",
            new_callable=AsyncMock,
            return_value=self._history,
        ))
        self._stack.enter_context(patch(
            "daemon.services.child_reports.get_last_assistant_timestamp",
            new_callable=AsyncMock,
            return_value=T_FRESH_ASSISTANT,
        ))
        self._stack.enter_context(patch(
            "daemon.services.child_reports.MainLoopBridge.run_async_no_wait"
        ))
        return self._stack

    def __exit__(self, *exc):
        return self._stack.__exit__(*exc)


@pytest.fixture
def gap_harness(gap_engine):
    """Minimal harness: real engine + repos + notify chain + lifecycle
    service (H10 sanctioned terminate lane). Mirrors the shape of
    ``strand_harness`` in
    ``tests/job_queue/test_watcher_loss_terminal_strand.py``."""
    instance_repo = SQLModelInstanceRepository(gap_engine)
    task_repo_fq = __import__(
        "daemon.repositories.task.repository", fromlist=["TaskRepository"]
    ).TaskRepository(gap_engine)
    job_repo = JobRepository(gap_engine)
    watcher_repo = JobWatcherRepository(gap_engine)
    from daemon.repositories.event.repository import EventRepository
    event_bus = __import__(
        "daemon.services.event_bus", fromlist=["EventBus"]
    ).EventBus(event_repo=EventRepository(gap_engine))

    manager = MagicMock()
    manager.engine = gap_engine
    manager._engine = gap_engine
    manager._instance_repository = instance_repo
    manager._task_repo = task_repo_fq
    manager._watcher_repo = watcher_repo
    manager._event_bus = event_bus
    from daemon.write_pause_guard import WritePauseGuard
    manager.write_guard = WritePauseGuard()
    manager._live_hub = MagicMock()
    manager._live_hub.stream_status_change = AsyncMock()
    manager._live_hub.stream_question_pack = AsyncMock()
    manager._live_hub.stream_answer_received = AsyncMock()
    manager.enqueue_message = AsyncMock(
        return_value=MagicMock(message_id="msg-gap")
    )
    manager.config = MagicMock()
    manager.config.report_repair = MagicMock()
    manager.config.report_repair.enabled = False
    manager.config.report_repair.repair_excluded_agents = []
    manager.config.report_repair.size_ratio_threshold = 5.0
    manager.config.report_repair.lookback_messages = 3
    manager.config.report_integrity = MagicMock()
    manager.config.report_integrity.b_terminal_waiting_guard_enabled = True

    resolver = WorkResolverService(
        task_repo=task_repo_fq,
        job_repo=job_repo,
        instance_repo=instance_repo,
    )
    queue_service = JobQueueService(
        repository=job_repo,
        lock_manager=MagicMock(),
        queue_repo=MagicMock(),
        instance_manager=manager,
    )
    queue_service.set_watcher_repo(watcher_repo)
    queue_service.set_work_resolver(resolver)
    manager._job_queue_service = queue_service

    events_service = MagicMock()
    events_service._publish_instance_lifecycle_event = AsyncMock()

    service = ChildReportsService(
        manager=manager, events_service=events_service,
    )
    from daemon.services.instance_lifecycle import InstanceLifecycleService
    manager._lifecycle_service = InstanceLifecycleService(
        manager,
        MagicMock(),
        events_service=events_service,
        job_queue_service=queue_service,
    )
    manager._child_reports_service = service
    # Default: no event-driven strand-healer wired (the gap tests
    # patch it directly where needed).
    manager._mission_strand_healer = None

    return type(
        "GapHarness",
        (),
        {
            "engine": gap_engine,
            "manager": manager,
            "instance_repo": instance_repo,
            "task_repo": task_repo_fq,
            "job_repo": job_repo,
            "watcher_repo": watcher_repo,
            "queue_service": queue_service,
            "events_service": events_service,
            "service": service,
        },
    )()


def _make_healer(harness):
    from daemon.services.mission_strand_healer import MissionStrandHealer
    return MissionStrandHealer(
        manager=harness.manager, interval_seconds=300,
    )


def _backdate_ghost(harness) -> None:
    """Backdate the ghost's ``updated_at`` past the healer's
    ORPHAN_MIN_AGE_SECONDS race guard (60s) so the safety-bound legs
    pass and only the targeted leg under test fails / aborts."""
    with harness.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE instances SET updated_at = :u "
                "WHERE instance_id = :g"
            ),
            {
                "u": (
                    datetime.now(timezone.utc)
                    - timedelta(hours=1)
                ).isoformat(),
                "g": GHOST,
            },
        )


# ─────────────────────────────────────────────────────────────────────────────
# G1 — scenario-3 conjunct: denial + completion compose
# ─────────────────────────────────────────────────────────────────────────────


class TestG1_DenialCompletionConjunct:
    """Commission scenario-3 conjunct: a denied spawn on the root's
    final turn must NOT prevent the mission from completing. The
    spawn-ACL denial leaves ZERO residue (pre-transaction gate,
    ``test_denied_spawn_leaves_zero_residue``); the root's completion
    path is independent (a different transactional system on the same
    engine). After the denial, driving the root to terminal must
    still stamp COMPLETED, fire the lifecycle completed event, and
    CAS-claim the armed watcher — same observable contract as the
    C1 clean-tree control."""

    @pytest.mark.asyncio
    async def test_denied_spawn_then_completion_publishes_and_fires(
        self, gap_harness,
    ):
        """Composition: deny-then-drive. The root is a clean tree
        (no ghost), so completion is the C1 contract; the spawn
        denial proves the spawn ACL is pre-transaction. Together,
        the steward observes BOTH simultaneously: a refused
        ``spawn_instance(agent_id='leader')`` AND a fired
        watcher row."""
        from daemon.tools.instance import create_instance_tools

        _seed_poison_tree(gap_harness.engine, with_ghost=False)
        _insert_receipt(gap_harness.engine)
        _seed_child_completed_event(
            gap_harness.engine, T_CHILD_COMPLETED,
        )

        manager = gap_harness.manager
        manager.spawn_instance = MagicMock(
            return_value=("should-never-spawn", None)
        )

        # Build the real spawn tool with the harness manager
        # (real DB — any stray write would land in the test engine).
        # 'leader' spawning 'leader' is denied by the real
        # ``_check_team_membership`` (leader's team_members excludes
        # itself).
        with patch("daemon.tools.instance.is_rag_enabled", return_value=False), \
             patch("daemon.tools.instance.create_rag_tools", return_value=[]), \
             patch("daemon.tools.instance.create_knowledge_tools", return_value=[]), \
             patch("daemon.tools.instance.create_inner_soul_tool", return_value=MagicMock()), \
             patch("daemon.tools.instance.create_access_memory_tool", return_value=MagicMock()), \
             patch("daemon.tools.instance.create_project_tools", return_value=[]), \
             patch("daemon.tools.instance.create_job_tools_if_available", return_value=[]), \
             patch("daemon.tools.instance.create_help_tool", return_value=MagicMock()), \
             patch("daemon.tools.instance.create_critical_notes_tools", return_value=[]), \
             patch("daemon.tools.instance.create_project_history_tools", return_value=[]), \
             patch("daemon.tools.instance.create_db_tools", return_value=[]), \
             patch("daemon.tools.instance.create_infra_tools", return_value=[]), \
             patch("daemon.tools.instance.create_context_tools", return_value=[]), \
             patch("daemon.tools.instance.create_chart_tools", return_value=[]), \
             patch("daemon.tools.instance.create_compare_tools", return_value=[]), \
             patch("daemon.tools.instance._load_mcp_tools", return_value=[]), \
             patch("daemon.tools.instance.scan_tools_for_full_docs"), \
             patch("daemon.tools.instance._apply_tool_filter",
                   side_effect=lambda tools, *a, **kw: tools):
            tools = create_instance_tools(
                manager, ROOT, "leader",
            )
        spawn_tool = next(
            t for t in tools if getattr(t, "name", None) == "spawn_instance"
        )

        # 1. DENIAL: spawn refused (pre-transaction gate).
        denial = await spawn_tool.coroutine(
            agent_id="leader",
            project_id="test-project",
        )
        assert denial.startswith("ERROR"), (
            f"denial expected (scenario-3 deny leg): {denial!r}"
        )
        manager.spawn_instance.assert_not_called()

        # 2. COMPLETION: drive the root. Same shape as
        #    test_c1_clean_tree_publishes_and_fires_watcher.
        with _DrivePatchStack(_fresh_history()):
            await (
                gap_harness.service
                ._process_child_completion_and_notify_parent(
                    ROOT, "root-final-msg-1",
                )
            )

        # 3. ROOT: stamped COMPLETED (the C1 contract holds after
        #    a denial — the two paths are independent).
        row = _get_instance_row(gap_harness.engine, ROOT)
        assert row.status == InstanceStatus.COMPLETED.value, (
            f"denial-then-completion: root must still reach COMPLETED "
            f"(got {row.status!r})"
        )

        # 4. LIFECYCLE COMPLETED event fires.
        completed = [
            c for c in gap_harness.events_service
            ._publish_instance_lifecycle_event.await_args_list
            if c.kwargs.get("status") == "completed"
            and c.kwargs.get("instance_id") == ROOT
        ]
        assert completed, (
            "denial-then-completion: lifecycle completed event must "
            "fire (the deny is pre-transaction; the publish path is "
            "independent and must run)"
        )

        # 5. WATCHER row is CAS-claimed.
        assert gap_harness.watcher_repo.get_watchers_for_job(
            RECEIPT_JOB
        ) == [], (
            "denial-then-completion: armed watcher row must be "
            "CAS-claimed (fired) once the mission closes"
        )

        # 6. NO STAMP-DOWNGRADE CHURN: the clean tree has no ghost,
        #    so the gate's recursive walk returns all-terminal (the
        #    stranding exclusion cannot fire on a clean tree).
        downgrade_sse = [
            c for c in gap_harness.manager._live_hub
            .stream_status_change.await_args_list
            if c.args and c.args[0] == ROOT
            and c.args[1] == "waiting_children"
        ]
        assert not downgrade_sse, (
            f"denial-then-completion: clean tree must NOT churn "
            f"through stamp→downgrade (got {downgrade_sse})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# G2a — scenario-6 healer lane: ghost probe raise → CLOSED
# G2b — scenario-6 guard lane:  ghost probe raise → mission held
# ─────────────────────────────────────────────────────────────────────────────


class TestG2_GhostProbeFailClosed:
    """Scenario-6 (healer + guard lane) fail-CLOSED contract on a
    raised ghost probe. The probe (``filter_never_dispatched_ids``)
    is the canonical never-dispatched signature oracle; a raise must
    NOT cause the system to act on rows it cannot prove are ghosts.

    * Healer lane: a healable stranded tree whose probe raises MUST
      NOT be healed — no terminate_instance, no orphan mutation, no
      root drive, root stays at WAITING_CHILDREN.
    * Guard lane: a tree whose only non-terminal member is the
      ghost MUST keep the mission held (``live=True``) when the
      probe raises — the strand exclusion cannot fire because the
      probe cannot prove the orphan's signature."""

    @pytest.mark.asyncio
    async def test_g2a_healer_probe_raise_fails_closed(
        self, gap_harness,
    ):
        """G2a (scenario-6, healer lane): monkeypatch
        ``filter_never_dispatched_ids`` to RAISE during the
        healer's sweep on an otherwise-healable stranded tree.
        Pin: ``healed=False``, NO ``terminate_instance`` call
        against the orphan, the orphan row is untouched (status
        still ``idle``), the root stays at WAITING_CHILDREN
        (no drive)."""
        _seed_poison_tree(gap_harness.engine, with_ghost=True)
        _insert_receipt(gap_harness.engine)
        _seed_child_completed_event(
            gap_harness.engine, T_CHILD_COMPLETED,
        )
        _backdate_ghost(gap_harness)

        # Spy on the lifecycle lane so we can assert NO call.
        terminate_called = []
        original_terminate = (
            gap_harness.manager._lifecycle_service.terminate_instance
        )

        async def _spy_terminate(instance_id, **kwargs):
            terminate_called.append((instance_id, kwargs))
            return await original_terminate(instance_id, **kwargs)

        gap_harness.manager._lifecycle_service.terminate_instance = (
            _spy_terminate
        )

        healer = _make_healer(gap_harness)
        with patch.object(
            gap_harness.instance_repo,
            "filter_never_dispatched_ids",
            side_effect=RuntimeError("probe forced raise"),
        ):
            with _DrivePatchStack(_fresh_history()):
                outcome = await healer.heal_instance(ROOT)

        # 1. healed=False (healer returns fail-closed).
        assert outcome["healed"] is False, (
            f"healer lane must fail-CLOSED on probe raise: {outcome!r}"
        )
        # 2. The reason names the probe failure (commission contract).
        assert "never-dispatched probe failed" in (
            outcome["reason"] or ""
        ), (
            f"reason must name the probe failure: {outcome!r}"
        )
        # 3. NO terminate_instance call against the orphan.
        assert terminate_called == [], (
            f"FAIL-CLOSED: no terminate_instance call on probe raise "
            f"(got {terminate_called!r})"
        )
        # 4. Orphan row is untouched (status still idle).
        ghost = _get_instance_row(gap_harness.engine, GHOST)
        assert ghost.status == InstanceStatus.IDLE.value, (
            f"orphan row must be untouched on probe raise "
            f"(got status={ghost.status!r})"
        )
        # 5. Root stays at WAITING_CHILDREN (no drive).
        root = _get_instance_row(gap_harness.engine, ROOT)
        assert root.status == InstanceStatus.WAITING_CHILDREN.value, (
            f"root must stay non-terminal on probe raise "
            f"(got status={root.status!r})"
        )
        # 6. Watcher survives (no fire on a no-op heal).
        assert (
            gap_harness.watcher_repo.get_watchers_for_job(RECEIPT_JOB)
        ), (
            "watcher must survive when the heal aborts (no fire on "
            "no-op)"
        )

    @pytest.mark.asyncio
    async def test_g2b_guard_probe_raise_keeps_mission_held(
        self, gap_harness,
    ):
        """G2b (scenario-6, guard lane): monkeypatch
        ``filter_never_dispatched_ids`` to RAISE during
        ``evaluate_mission_live`` on a tree whose ONLY non-terminal
        member is the never-dispatched ghost. Pin:
        ``verdict.live=True`` (mission held) — the strand exclusion
        cannot fire because the probe cannot prove the orphan's
        signature, and the guard fail-closed direction is to KEEP
        the mission held.

        The wrapper at ``evaluate_mission_live`` fail-OPENS on an
        INTERNAL exception (``live=False, error=True``); the
        fail-CLOSED strand probe exception is CAUGHT inside
        ``_evaluate_legs`` so it does NOT propagate up — the
        verdict stays ``error=False``, ``live=True``."""
        _seed_poison_tree(gap_harness.engine, with_ghost=True)

        with patch.object(
            gap_harness.instance_repo,
            "filter_never_dispatched_ids",
            side_effect=RuntimeError("probe forced raise"),
        ):
            verdict = await _mlg.evaluate_mission_live(
                instance_repository=gap_harness.instance_repo,
                instance_id=ROOT,
            )

        # FAIL-CLOSED on the strand lane: the strand exclusion
        # cannot prove the ghost signature, so the ghost is NOT
        # excluded, the orphan still counts as non-terminal, and
        # the mission is held.
        assert verdict.live is True, (
            f"FAIL-CLOSED: a raised strand probe must keep the "
            f"mission held (got live={verdict.live!r}, "
            f"reason={verdict.reason!r})"
        )
        # The wrapper fail-OPEN path (``error=True``) would set
        # ``live=False``; the fail-CLOSED strand catch leaves
        # ``error=False``.
        assert verdict.error is False, (
            "the strand-probe raise is caught inside the guard "
            "(fail-CLOSED direction); the wrapper fail-OPEN path "
            "(error=True) must NOT trigger"
        )


# ─────────────────────────────────────────────────────────────────────────────
# G3 — scenario-6: PAUSED straggler under TERMINAL parent at HEALER
# ─────────────────────────────────────────────────────────────────────────────


class TestG3_PausedStragglerAtHealer:
    """Scenario-6 (healer): a PAUSED straggler under a TERMINAL
    parent is NOT never-dispatched-shaped (status != idle, the
    spawn-then-message ritual has progressed past the never-
    dispatched window) — the safety-bound leg
    (``mission_strand_healer.py:668-679``) aborts the heal before
    any termination runs. The PAUSED row stays untouched and the
    root stays at WAITING_CHILDREN."""

    @pytest.mark.asyncio
    async def test_paused_straggler_aborts_heal_no_termination(
        self, gap_harness,
    ):
        _seed_poison_tree(gap_harness.engine, with_ghost=False)
        # Insert a PAUSED straggler under the COMPLETED tester
        # parent (a "stranded-but-not-strand-shaped" orphan — the
        # PAUSED state carries execution evidence: pause-cascade
        # already ran on this row; the operator owns it).
        _insert_instance(
            gap_harness.engine, "straggler-paused-gap",
            status=InstanceStatus.PAUSED.value,
            parent_id=TESTER,
            agent_id="worker",
            last_activity_at=now_utc_naive() - timedelta(hours=1),
        )

        # Spy on the lifecycle lane.
        terminate_called = []
        original_terminate = (
            gap_harness.manager._lifecycle_service.terminate_instance
        )

        async def _spy_terminate(instance_id, **kwargs):
            terminate_called.append((instance_id, kwargs))
            return await original_terminate(instance_id, **kwargs)

        gap_harness.manager._lifecycle_service.terminate_instance = (
            _spy_terminate
        )

        healer = _make_healer(gap_harness)
        outcome = await healer.heal_instance(ROOT)

        # Healer aborts (PAUSED is not never-dispatched-shaped).
        assert outcome["healed"] is False, (
            f"PAUSED straggler must abort the heal (got {outcome!r})"
        )
        # Reason names the fail-closed class — the safety-bound leg
        # is the canonical entry, the PAUSED status is the trigger.
        reason = outcome.get("reason") or ""
        assert (
            "not never-dispatched" in reason
            or "not terminal" in reason
        ), (
            f"reason must name the safety-bound/fail-closed leg: "
            f"{reason!r}"
        )
        # No termination (the safety-bound leg fires BEFORE the
        # termination loop).
        assert terminate_called == [], (
            f"FAIL-CLOSED: no terminate_instance on PAUSED straggler "
            f"(got {terminate_called!r})"
        )
        # The PAUSED row is untouched.
        paused_row = _get_instance_row(
            gap_harness.engine, "straggler-paused-gap"
        )
        assert paused_row.status == InstanceStatus.PAUSED.value, (
            f"PAUSED straggler must be untouched "
            f"(got status={paused_row.status!r})"
        )
        # Root stays at WAITING_CHILDREN (no drive).
        root = _get_instance_row(gap_harness.engine, ROOT)
        assert root.status == InstanceStatus.WAITING_CHILDREN.value, (
            f"root must stay non-terminal on PAUSED straggler "
            f"(got status={root.status!r})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# G4 — scenario-9: UNKNOWN-parent ghost (parent row missing)
# ─────────────────────────────────────────────────────────────────────────────


class TestG4_UnknownParentGhost:
    """Scenario-9 (guard + healer): a never-dispatched ghost whose
    DIRECT parent row is MISSING from the repository read (UNKNOWN
    parent status). Both lanes must fail closed:

    * Guard lane: ``evaluate_mission_live`` — the missing parent
      cannot be terminal (status unknown), so the orphan does NOT
      satisfy the strand signature and is NOT excluded; the
      non-terminal orphan keeps the mission held (``live=True``).
    * Healer lane: ``heal_instance`` — the safety-bound leg
      (``parent_status is None``) aborts the heal; no
      ``terminate_instance``, no drive."""

    @pytest.mark.asyncio
    async def test_g4_guard_unknown_parent_keeps_mission_held(
        self, gap_harness,
    ):
        """G4 guard: the orphan has a parent_id FK pointing at a
        row that is absent from the tree-walk enumeration
        (``status_by_id[parent_id]`` is ``None``). The strand
        exclusion's terminal-ancestry check fails — the parent
        cannot be proven terminal — so the orphan is NOT
        excluded; the non-terminal orphan keeps the mission
        held (``live=True``)."""
        _seed_poison_tree(gap_harness.engine, with_ghost=True)

        # Monkeypatch the tree walk to EXCLUDE TESTER — same
        # mechanism as the healer test. The orphan stays reachable
        # via the permanent lineage but its direct parent's row
        # is absent from the walk.
        original_get_tree = (
            gap_harness.instance_repo.get_tree_ids_permanent
        )

        def _walk_without_tester(root_id):
            return [
                tid for tid in original_get_tree(root_id)
                if tid != TESTER
            ]

        with patch.object(
            gap_harness.instance_repo,
            "get_tree_ids_permanent",
            side_effect=_walk_without_tester,
        ):
            verdict = await _mlg.evaluate_mission_live(
                instance_repository=gap_harness.instance_repo,
                instance_id=ROOT,
            )

        assert verdict.live is True, (
            f"FAIL-CLOSED: UNKNOWN-parent ghost must keep the "
            f"mission held (got live={verdict.live!r}, "
            f"reason={verdict.reason!r})"
        )
        assert verdict.error is False, (
            "no internal exception — the unknown-parent branch is "
            "a normal guard path, not an error"
        )

    @pytest.mark.asyncio
    async def test_g4_healer_unknown_parent_aborts(self, gap_harness):
        """G4 healer: the orphan's direct parent is missing from the
        tree-walk enumeration (``get_tree_ids_permanent`` skips the
        parent), so ``status_by_id[parent_id]`` returns ``None``.
        The safety-bound leg (``parent_status is None``) aborts the
        heal; no terminate, no drive."""
        _seed_poison_tree(gap_harness.engine, with_ghost=True)
        _backdate_ghost(gap_harness)

        # Monkeypatch the tree walk to EXCLUDE TESTER — simulates
        # "parent row missing from the walk" (the orphan stays
        # reachable via the permanent lineage but its direct
        # parent's row is absent). This is the canonical
        # expression of ``status_by_id[parent_id] is None`` in
        # the healer's safety-bound check.
        original_get_tree = (
            gap_harness.instance_repo.get_tree_ids_permanent
        )

        def _walk_without_tester(root_id):
            return [
                tid for tid in original_get_tree(root_id)
                if tid != TESTER
            ]

        terminate_called = []
        original_terminate = (
            gap_harness.manager._lifecycle_service.terminate_instance
        )

        async def _spy_terminate(instance_id, **kwargs):
            terminate_called.append((instance_id, kwargs))
            return await original_terminate(instance_id, **kwargs)

        gap_harness.manager._lifecycle_service.terminate_instance = (
            _spy_terminate
        )

        with patch.object(
            gap_harness.instance_repo,
            "get_tree_ids_permanent",
            side_effect=_walk_without_tester,
        ):
            healer = _make_healer(gap_harness)
            outcome = await healer.heal_instance(ROOT)

        assert outcome["healed"] is False, (
            f"UNKNOWN-parent ghost must abort the heal "
            f"(got {outcome!r})"
        )
        # Reason names the safety-bound (parent not terminal —
        # here, parent is unknown/None).
        reason = outcome.get("reason") or ""
        assert "not terminal" in reason, (
            f"reason must name the safety-bound leg: {reason!r}"
        )
        assert terminate_called == [], (
            f"FAIL-CLOSED: no terminate on unknown-parent ghost "
            f"(got {terminate_called!r})"
        )
        # Orphan row untouched.
        ghost = _get_instance_row(gap_harness.engine, GHOST)
        assert ghost.status == InstanceStatus.IDLE.value, (
            f"orphan must stay untouched (got {ghost.status!r})"
        )
        # Root stays at WAITING_CHILDREN (no drive).
        root = _get_instance_row(gap_harness.engine, ROOT)
        assert root.status == InstanceStatus.WAITING_CHILDREN.value, (
            f"root must stay non-terminal (got {root.status!r})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# G5 — scenario-9: root ALREADY-TERMINAL on healer sweep
# ─────────────────────────────────────────────────────────────────────────────


class TestG5_AlreadyTerminalRoot:
    """Scenario-9: a root that is ALREADY-TERMINAL when the healer
    looks (e.g. terminalized by an operator mid-cycle) is a
    production no-op — the wedge-posture check
    (``mission_strand_healer.py:563-568``) returns
    ``healed=False`` immediately without touching any row."""

    @pytest.mark.asyncio
    async def test_already_terminal_root_is_noop(
        self, gap_harness,
    ):
        _seed_poison_tree(gap_harness.engine, with_ghost=True)
        # Stamp the root to a terminal posture OUTSIDE the healer's
        # canonical path (operator-style cleanup). The ghost is
        # still idle in the row state — the healer's first check
        # must short-circuit on the root status BEFORE evaluating
        # the orphan.
        with gap_harness.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE instances SET status = 'completed' "
                    "WHERE instance_id = :r"
                ),
                {"r": ROOT},
            )

        terminate_called = []
        original_terminate = (
            gap_harness.manager._lifecycle_service.terminate_instance
        )

        async def _spy_terminate(instance_id, **kwargs):
            terminate_called.append((instance_id, kwargs))
            return await original_terminate(instance_id, **kwargs)

        gap_harness.manager._lifecycle_service.terminate_instance = (
            _spy_terminate
        )

        healer = _make_healer(gap_harness)
        outcome = await healer.heal_instance(ROOT)

        # Healer returns no-op.
        assert outcome["healed"] is False, (
            f"already-terminal root must be a no-op "
            f"(got {outcome!r})"
        )
        reason = outcome.get("reason") or ""
        assert "not the wedge posture" in reason or (
            "waiting_children" in reason
        ), (
            f"reason must name the wedge-posture mismatch: {reason!r}"
        )
        # No terminate (orphan untouched — the wedge-posture check
        # short-circuits before the tree walk).
        assert terminate_called == [], (
            f"already-terminal root: no terminate "
            f"(got {terminate_called!r})"
        )
        # Root stays COMPLETED.
        root = _get_instance_row(gap_harness.engine, ROOT)
        assert root.status == InstanceStatus.COMPLETED.value, (
            f"already-terminal root must stay terminal "
            f"(got {root.status!r})"
        )
        # Orphan stays idle (no walk ran).
        ghost = _get_instance_row(gap_harness.engine, GHOST)
        assert ghost.status == InstanceStatus.IDLE.value, (
            f"orphan must stay untouched on no-op "
            f"(got {ghost.status!r})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# G6 — scenario-9: double-heal idempotency
# ─────────────────────────────────────────────────────────────────────────────


class TestG6_DoubleHealIdempotency:
    """Scenario-9: once a heal succeeded in one ``sweep_once``, a
    subsequent ``sweep_once`` is a no-op. The root's status flips
    to terminal via the drive; the second sweep's
    ``_find_wc_root_ids`` no longer enumerates a non-wedge root
    (the SQL filter is ``status == 'waiting_children'``), so the
    second invocation either scans 0 roots (if no other wedged roots
    exist) or finds zero new wedge postures to act on."""

    @pytest.mark.asyncio
    async def test_second_sweep_after_heal_is_noop(
        self, gap_harness,
    ):
        """Run ``sweep_once`` twice in sequence. The first invocation
        heals the wedge (``scanned=1, healed=1``); the second is a
        no-op — the root has flipped to terminal so the SQL
        enumeration no longer finds it (``scanned=0``); no new
        terminate, no new publish, no new lifecycle event."""
        _seed_poison_tree(gap_harness.engine, with_ghost=True)
        _insert_receipt(gap_harness.engine)
        _seed_child_completed_event(
            gap_harness.engine, T_CHILD_COMPLETED,
        )
        _backdate_ghost(gap_harness)

        healer = _make_healer(gap_harness)

        # ── First sweep: heals the wedge. ─────────────────────────
        with _DrivePatchStack(_fresh_history()):
            stats1 = await healer.sweep_once()

        assert stats1["scanned"] == 1
        assert stats1["healed"] == 1
        assert stats1["terminalized_orphans"] == 1
        root = _get_instance_row(gap_harness.engine, ROOT)
        assert root.status == InstanceStatus.COMPLETED.value
        ghost_after_first = _get_instance_row(gap_harness.engine, GHOST)
        assert ghost_after_first.status == "terminated"
        # Snapshot the publish/lifecycle event counts AFTER sweep #1.
        lifecycle_calls_after_1 = (
            gap_harness.events_service
            ._publish_instance_lifecycle_event.await_count
        )

        # ── Second sweep: idempotent no-op. ───────────────────────
        # Wrap the lifecycle lane with a spy so we can prove NO
        # new terminate call runs (scanned=0 must mean the loop
        # never reaches the terminate seam).
        terminate_calls: list[str] = []
        original_terminate = (
            gap_harness.manager._lifecycle_service.terminate_instance
        )

        async def _spy_terminate(instance_id, **kwargs):
            terminate_calls.append(instance_id)
            return await original_terminate(instance_id, **kwargs)

        gap_harness.manager._lifecycle_service.terminate_instance = (
            _spy_terminate
        )
        healer2 = _make_healer(gap_harness)
        stats2 = await healer2.sweep_once()

        # The root is no longer WAITING_CHILDREN so the SQL filter
        # excludes it from the enumeration → scanned=0. No new
        # heal, no new termination.
        assert stats2["scanned"] == 0, (
            f"second sweep must scan zero non-wedge roots "
            f"(got {stats2!r})"
        )
        assert stats2["healed"] == 0, (
            f"second sweep must not heal again (got {stats2!r})"
        )
        assert stats2["terminalized_orphans"] == 0, (
            f"second sweep must not re-terminalize "
            f"(got {stats2!r})"
        )
        # Lifecycle terminate spy: nothing called (the loop never
        # reached heal_instance).
        assert terminate_calls == [], (
            f"second sweep must NOT call terminate_instance "
            f"(got {terminate_calls!r})"
        )
        # Lifecycle publish count unchanged across the second sweep.
        lifecycle_calls_after_2 = (
            gap_harness.events_service
            ._publish_instance_lifecycle_event.await_count
        )
        assert lifecycle_calls_after_2 == lifecycle_calls_after_1, (
            f"second sweep must not publish new lifecycle events "
            f"(before={lifecycle_calls_after_1}, "
            f"after={lifecycle_calls_after_2})"
        )
        # Ghost stays terminated (from sweep #1).
        ghost_after_second = _get_instance_row(gap_harness.engine, GHOST)
        assert ghost_after_second.status == "terminated", (
            f"ghost must stay terminated across the second sweep "
            f"(got {ghost_after_second.status!r})"
        )
        # Root stays COMPLETED.
        root_after = _get_instance_row(gap_harness.engine, ROOT)
        assert root_after.status == InstanceStatus.COMPLETED.value


# ─────────────────────────────────────────────────────────────────────────────
# G8 — fidelity-pin: pre-stamp eval fail-OPEN by design
# ─────────────────────────────────────────────────────────────────────────────


class TestG8_PreStampEvalFailOpen:
    """G8 fidelity-audit pin: the pre-stamp liveness eval
    (``child_reports.py:2613-2650``, C3 contract) is INTENTIONALLY
    fail-OPEN — any exception logs a WARNING and the code proceeds
    to the sync stamp. The at-least-once terminal-delivery
    guarantee outranks the churn-avoidance optimization. This is the
    asymmetric boundary:

    * Healer probe (``filter_never_dispatched_ids``) and guard
      strand probe: fail-CLOSED (G2a/G2b) — when the oracle
      cannot prove the strand signature, the system must NOT
      act on rows it cannot characterize.
    * Pre-stamp eval: fail-OPEN — when the eval cannot be
      performed, the system proceeds to the stamp and lets the
      downstream stage-6 ``_root_completion_gate`` be the
      authoritative defense-in-depth check.

    Docstring per task: "fidelity-pin: pre-stamp eval fail-OPEN
    by design (C3); healer probe fail-CLOSED pinned by G2a"."""

    @pytest.mark.asyncio
    async def test_pre_stamp_eval_raise_proceeds_to_stamp(
        self, gap_harness,
    ):
        """Stranded-tree shape: poison tree with ghost. Monkeypatch
        ``evaluate_mission_live`` (the function imported into
        ``child_reports`` at :2615-2617, called inside the ROOT
        lane's pre-stamp eval) to RAISE.

        Pin the CURRENT BY-DESIGN contract:
        1. The exception is SWALLOWED (C3 catch at :2644-2650).
        2. The sync stamp PROCEEDS (fail-OPEN — at-least-once
           terminal delivery outranks churn avoidance).
        3. The downstream stage-6 ``_root_completion_gate`` is the
           defense-in-depth — for the stranded tree shape, the
           strand-aware recursive walk excludes the ghost and the
           gate PASSES, so the publish fires (root → COMPLETED,
           lifecycle completed event, watcher CAS-claimed)."""
        _seed_poison_tree(gap_harness.engine, with_ghost=True)
        _insert_receipt(gap_harness.engine)
        _seed_child_completed_event(
            gap_harness.engine, T_CHILD_COMPLETED,
        )

        # Patch the canonical ``evaluate_mission_live`` source —
        # the relative import inside ``child_reports._process_child_
        # completion_and_notify_parent`` reads through this module
        # attribute each invocation, so the patch takes effect
        # for the pre-stamp eval call site.
        with patch(
            "daemon.services.mission_live_guard.evaluate_mission_live",
            side_effect=RuntimeError(
                "G8 fidelity-audit: probe forced raise"
            ),
        ):
            with _DrivePatchStack(_fresh_history()):
                await (
                    gap_harness.service
                    ._process_child_completion_and_notify_parent(
                        ROOT, "root-final-msg-1",
                    )
                )

        # ── 1. Pre-stamp eval raised, but the FAIL-OPEN catch
        #    swallowed the exception and the sync stamp PROCEEDED.
        #    Root reached COMPLETED — the stamp was NOT skipped.
        row = _get_instance_row(gap_harness.engine, ROOT)
        assert row.status == InstanceStatus.COMPLETED.value, (
            "FAIL-OPEN contract (C3, child_reports.py:2644-2650): "
            "the pre-stamp eval raise must NOT skip the sync stamp "
            f"(got root status={row.status!r})"
        )

        # ── 2. Downstream defense-in-depth: the stage-6
        #    ``_root_completion_gate`` ran on the stranded-tree
        #    shape. The strand-aware recursive walk excludes the
        #    ghost (the live RCA tree, post-component-2), so the
        #    gate PASSES → publish fires.
        completed = [
            c for c in gap_harness.events_service
            ._publish_instance_lifecycle_event.await_args_list
            if c.kwargs.get("status") == "completed"
            and c.kwargs.get("instance_id") == ROOT
        ]
        assert completed, (
            "downstream defense-in-depth: stage-6 gate must "
            "publish the completed event for the stranded-tree "
            "shape (strand-aware walk excludes the ghost)"
        )

        # ── 3. Watcher row is CAS-claimed (the receipt
        #    finalizes via the existing observer path).
        assert gap_harness.watcher_repo.get_watchers_for_job(
            RECEIPT_JOB
        ) == [], (
            "FAIL-OPEN: the armed watcher row must be CAS-claimed "
            "(fired) on the publish chain"
        )

        # ── 4. No stamp→downgrade churn: the pre-stamp eval
        #    raised, but the stamp PROCEEDED (fail-OPEN); the
        #    downstream gate PASSED for the stranded tree, so
        #    there is no downgrade SSE event.
        downgrade_sse = [
            c for c in gap_harness.manager._live_hub
            .stream_status_change.await_args_list
            if c.args and c.args[0] == ROOT
            and c.args[1] == "waiting_children"
        ]
        assert not downgrade_sse, (
            f"FAIL-OPEN proceeds past the eval — the gate PASSES "
            f"for the stranded tree (no downgrade): {downgrade_sse}"
        )


class TestG7_PendingTaskAbortsHeal:
    """Healer zero-obligation leg (3) — a PENDING ``task`` row for
    the root is the (in-flight turn) carrier (an LLM is currently
    processing this work item); the wedge will close via the natural
    path on completion, NOT via the healer. The healer must abort
    (``mission_strand_healer.py:603-612``) without driving."""

    @pytest.mark.asyncio
    async def test_pending_task_row_aborts_heal(
        self, gap_harness,
    ):
        _seed_poison_tree(gap_harness.engine, with_ghost=True)
        _backdate_ghost(gap_harness)

        # Insert a PENDING task for the root — the wedge will close
        # via the natural turn-completion path, not the healer.
        with gap_harness.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO task
                        (task_type, instance_id, status, retry_count,
                         created_at, cancel_requested, retry_scheduled,
                         work_id, is_deferred, is_background,
                         completed_at)
                    VALUES
                        ('process_message', :instance_id, 'pending', 0,
                         :created_at, 0, 0, :work_id, 0, 0, NULL)
                    """
                ),
                {
                    "instance_id": ROOT,
                    "created_at": (
                        datetime.now(timezone.utc).isoformat()
                    ),
                    "work_id": str(uuid.uuid4()),
                },
            )

        terminate_called = []
        original_terminate = (
            gap_harness.manager._lifecycle_service.terminate_instance
        )

        async def _spy_terminate(instance_id, **kwargs):
            terminate_called.append((instance_id, kwargs))
            return await original_terminate(instance_id, **kwargs)

        gap_harness.manager._lifecycle_service.terminate_instance = (
            _spy_terminate
        )

        healer = _make_healer(gap_harness)
        outcome = await healer.heal_instance(ROOT)

        # Heal aborts on leg (3).
        assert outcome["healed"] is False, (
            f"PENDING task must abort the heal (got {outcome!r})"
        )
        reason = outcome.get("reason") or ""
        assert "pending_tasks" in reason, (
            f"reason must name the PENDING-task leg: {reason!r}"
        )
        # No terminate, no drive.
        assert terminate_called == [], (
            f"PENDING task leg: no terminate (got {terminate_called!r})"
        )
        # Root stays WAITING_CHILDREN.
        root = _get_instance_row(gap_harness.engine, ROOT)
        assert root.status == InstanceStatus.WAITING_CHILDREN.value, (
            f"root must stay non-terminal on PENDING-task abort "
            f"(got {root.status!r})"
        )
        # Ghost untouched (orphan walk never ran — the pending-task
        # check fires BEFORE the tree-walk legs).
        ghost = _get_instance_row(gap_harness.engine, GHOST)
        assert ghost.status == InstanceStatus.IDLE.value, (
            f"orphan must be untouched (got {ghost.status!r})"
        )

# ─────────────────────────────────────────────────────────────────────────────
# G9 — final hardening: read-side age mirror (shared race-guard predicate)
# ─────────────────────────────────────────────────────────────────────────────


def _stamp_ghost_age(harness, *, aged: bool) -> None:
    """Re-stamp the ghost's ``updated_at`` young (just-spawned) or
    aged (5 min — well past the shared ORPHAN_MIN_AGE_SECONDS guard).
    The seed models the aged steady state; the young variant is the
    explicitly-tested exception."""
    with harness.engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE instances SET updated_at = :u "
                "WHERE instance_id = :g"
            ),
            {
                "u": (
                    datetime.now(timezone.utc)
                    - (timedelta(minutes=5) if aged else timedelta(0))
                ).isoformat(),
                "g": GHOST,
            },
        )


class TestG9_ReadSideAgeMirror:
    """Final hardening (2026-10-14): the read-side strand exclusion
    runs the SAME ``strand_past_race_guard`` predicate as the
    write-side healer (single source of truth in
    ``mission_live_guard``). A too-fresh ghost keeps holding liveness
    (the just-messaged-not-yet-rowed window); only an AGED ghost is
    excluded. Pins both directions of the mirror."""

    @pytest.mark.asyncio
    async def test_fresh_ghost_under_terminal_parent_keeps_mission_held(
        self, gap_harness,
    ):
        """A just-spawned never-dispatched ghost (<60s) under a
        TERMINAL direct parent must NOT be excluded — the
        spawn-then-message ritual may still deliver its first
        message, so the row keeps holding mission liveness (same
        race-guard philosophy as the healer's write side)."""
        _seed_poison_tree(gap_harness.engine, with_ghost=True)
        _stamp_ghost_age(gap_harness, aged=False)
        # Stamp the root (the emission-gate-time posture — the sync
        # helper has already stamped COMPLETED when the gate walks).
        with gap_harness.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE instances SET status = 'completed' "
                    "WHERE instance_id = :r"
                ),
                {"r": ROOT},
            )

        verdict = await _mlg.evaluate_mission_live(
            instance_repository=gap_harness.instance_repo,
            instance_id=ROOT,
        )

        assert verdict.live is True, (
            f"FAIL-CLOSED: a too-fresh ghost must keep holding "
            f"liveness (got live={verdict.live!r}, "
            f"reason={verdict.reason!r})"
        )
        assert verdict.error is False
        assert "strand member(s) excluded" not in verdict.reason, (
            "the fresh ghost must NOT have been strand-excluded"
        )

    @pytest.mark.asyncio
    async def test_aged_ghost_under_terminal_parent_is_excluded(
        self, gap_harness,
    ):
        """The aged steady-state shape (the seed): ghost ≥60s old
        under a TERMINAL direct parent is excluded — the mission is
        NOT live (the original strand fix contract, now age-gated)."""
        _seed_poison_tree(gap_harness.engine, with_ghost=True)
        _stamp_ghost_age(gap_harness, aged=True)
        with gap_harness.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE instances SET status = 'completed' "
                    "WHERE instance_id = :r"
                ),
                {"r": ROOT},
            )

        verdict = await _mlg.evaluate_mission_live(
            instance_repository=gap_harness.instance_repo,
            instance_id=ROOT,
        )

        assert verdict.live is False, (
            f"aged stranded ghost must not hold liveness "
            f"(got: {verdict.reason!r})"
        )
        assert verdict.error is False
        assert "strand member(s) excluded" in verdict.reason


# ─────────────────────────────────────────────────────────────────────────────
# G10 — final hardening: TRUE-CONCURRENT heal (per-root lock pin)
# ─────────────────────────────────────────────────────────────────────────────


class TestG10_HealConcurrency:
    """Final hardening (2026-10-14): the 300s sweep and the
    event-driven downgrade hand-off can target the SAME root
    concurrently. ``MissionStrandHealer.heal_instance`` serializes
    per root behind an ``asyncio.Lock``: two concurrent invocations
    produce EXACTLY ONE heal — the second caller waits, re-runs the
    invariant checks against the post-heal state, and observes the
    already-healed root cleanly (the ``not the wedge posture`` skip).
    Pins the lock AND its refcounted map hygiene (entries popped when
    the last caller leaves)."""

    @pytest.mark.asyncio
    async def test_concurrent_heals_terminal_events_exactly_once(
        self, gap_harness,
    ):
        import asyncio as _asyncio

        _seed_poison_tree(gap_harness.engine, with_ghost=True)
        _insert_receipt(gap_harness.engine)
        _seed_child_completed_event(
            gap_harness.engine, T_CHILD_COMPLETED,
        )
        healer = _make_healer(gap_harness)

        # Spy on the sanctioned terminate lane — exactly one ghost
        # terminate may run across BOTH callers.
        terminate_calls: list[str] = []
        original_terminate = (
            gap_harness.manager._lifecycle_service.terminate_instance
        )

        async def _spy_terminate(instance_id, **kwargs):
            terminate_calls.append(instance_id)
            return await original_terminate(instance_id, **kwargs)

        gap_harness.manager._lifecycle_service.terminate_instance = (
            _spy_terminate
        )

        # TRUE concurrency: both coroutines are scheduled by the same
        # gather; the loser blocks on the per-root lock mid-flight
        # (not a sequential second call).
        with _DrivePatchStack(_fresh_history()):
            results = await _asyncio.gather(
                healer.heal_instance(ROOT),
                healer.heal_instance(ROOT),
            )

        # Exactly ONE caller performed the heal; the other observed
        # the already-healed state cleanly (no exception — gather
        # would have raised; a named skip reason).
        healed = [r for r in results if r.get("healed")]
        skipped = [r for r in results if not r.get("healed")]
        assert len(healed) == 1, f"exactly one heal (got {results!r})"
        assert len(skipped) == 1, f"exactly one clean skip (got {results!r})"
        assert "wedge posture" in (skipped[0].get("reason") or ""), (
            f"the second caller must observe the healed state via the "
            f"wedge-posture skip (got {skipped[0]!r})"
        )

        # Terminal surface fired EXACTLY ONCE:
        # (a) one ghost terminate through the lifecycle lane;
        assert terminate_calls == [GHOST], (
            f"exactly one ghost terminate across both callers "
            f"(got {terminate_calls!r})"
        )
        # (b) ONE root terminal lifecycle event across BOTH calls
        #     (no duplicate SSE/lifecycle noise from interleaved
        #     drives);
        root_terminal_events = [
            c for c in (
                gap_harness.events_service
                ._publish_instance_lifecycle_event.await_args_list
            )
            if c.kwargs.get("instance_id") == ROOT
            and c.kwargs.get("status") == "completed"
        ]
        assert len(root_terminal_events) == 1, (
            f"root terminal lifecycle event must fire EXACTLY ONCE "
            f"across concurrent heals (got "
            f"{len(root_terminal_events)}: {[dict(c.kwargs) for c in root_terminal_events]!r})"
        )
        # (c) the watcher delivery rode the transport boundary EXACTLY
        #     ONCE across both callers (the CAS-claimed notify lane).
        assert (
            gap_harness.manager.enqueue_message.await_count == 1
        ), (
            f"watcher delivery must enqueue exactly once across "
            f"concurrent heals (got "
            f"{gap_harness.manager.enqueue_message.await_count})"
        )

        # End state identical to the sequential heal (G6 shape).
        assert _get_instance_row(
            gap_harness.engine, ROOT
        ).status == InstanceStatus.COMPLETED.value
        assert _get_instance_row(
            gap_harness.engine, GHOST
        ).status == "terminated"

        # Lock-map hygiene: both callers left — the per-root entries
        # are popped (bounded growth; no stale lock leak).
        assert healer._heal_locks == {}, (
            f"per-root lock map must drain (got {healer._heal_locks!r})"
        )
        assert healer._heal_lock_refs == {}, (
            f"per-root refcount map must drain "
            f"(got {healer._heal_lock_refs!r})"
        )
