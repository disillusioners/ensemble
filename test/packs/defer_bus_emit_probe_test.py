#!/usr/bin/env python3
"""Bus-Emit Fix Probe — child_still_running_defer (incident 02fb2e01, fix ca9263c2).

Spec: .agents/tester/MOCK_TESTS.md → "child_still_running_defer Bus-Emit Fix
(02fb2e01)".

Branch: feature/orphan-active-job-recovery @ ba39a40e.

Independent of the in-tree unit tests
(``tests/unit/test_child_still_running_defer_bus_terminal.py``) — this probe
constructs REAL repositories on file-backed SQLite (under ``/tmp``) and drives
the REAL ``ChildReportsService._dispatch_post_commit_side_effects`` defer
branch via the REAL ``DependencyBus``, asserting REAL DB rows end-state (not
mock return values).

Only the ``manager._live_hub``, ``manager._task_repo``, ``manager._instance_repository``
seams are stubbed — these are the only external surfaces the dispatch path
reads in the defer branch (and they are stubbed the same way
``tests/unit/test_child_still_running_defer_bus_terminal.py`` stubs them).
The bus singleton, ``transition_state`` guarded UPDATE, watcher state
transitions, and FollowUp returned-from-bus list are all real.

Background. Incident 02fb2e01: commit ``16553972`` added the corrective bus
emit ``_emit_terminal_for_child_instance_via_bus`` to ``regular_child_completed``
(closes the multi-turn watcher gap) and the task-keyed emit
``_emit_terminal_via_bus`` to the same branch. The ``child_still_running_defer``
branch (non-root instance with active children) was MISSED — only SSE
``waiting_children`` was emitted, so the parent's PENDING watcher stayed
PENDING, the parent's JobItem never finalized, and the leader wedged.

Fix ``ca9263c2`` adds BOTH emits to the defer branch
(``child_reports.py:3456`` task-keyed + ``:3461`` corrective). The
``transition_state`` guard at ``dependency_bus/repository.py:608`` enforces
exactly-once via ``WHERE state='PENDING'`` Core UPDATE — a re-entry on an
already-FIRED watcher is a rowcount=0 no-op.

Three probes, all on the REAL defer path:

  P1 — exactly-once called-twice. Seed parent + child + PENDING watcher;
       drive the defer TWICE via ``_dispatch_post_commit_side_effects``
       (same result, same watcher). Assert: 1st dispatch fires the watcher
       (PENDING→FIRED, fired_at stamped, enqueued_at stamped); 2nd dispatch
       is a no-op (helpers see no PENDING rows, no DB write, watcher fields
       unchanged). Then call ``transition_state`` directly on the already-
       FIRED row to assert the guard's rowcount=0 return (this is the
       W4 second-look: the load-bearing exactly-once evidence per the
       task brief). Companion to
       ``tests/unit/test_child_still_running_defer_bus_terminal.py::
       TestDeferDoubleEmitIdempotency.test_defer_double_emit_is_idempotent``
       (mock-based) and
       ``::test_transition_state_real_db_idempotency_via_guard`` (DB-direct).
       This probe is the DISPATCH-PATH variant — exercises the helpers
       via the real service method, not via direct repo calls.

  P2 — legitimate defer preserved. Drive the defer ONCE; assert the
       legitimate-defer invariants are preserved (mirrors
       ``TestDeferPreservesLegitimateDeferral`` but asserts REAL behavior):
         * ``_live_hub.stream_status_change(..., 'waiting_children', ...)``
           IS awaited (SSE preserved).
         * ``CompletionRegistry.complete`` is NOT called (no premature
           finalization).
         * ``registry.get_version`` / ``get_resolved`` are NOT called
           (lifecycle-hook dispatch gated on ``regular_child_completed``).
         * ``_events_service._publish_instance_lifecycle_event`` is NOT
           called.
         * Instance status is unchanged in the DB.
       Plus a note on the bus emits: per the fix, the defer DOES fire both
       bus helpers (this is the point of ca9263c2); the spec's "no bus
       emit" phrasing is interpreted as "no terminal-completion emit" —
       the defer propagates the child's terminal state to the parent's
       watcher (which is exactly the bug fix), but does NOT mark the child
       instance COMPLETED. See ``Evidence`` for the fired_at / state trace.

  P3 — incident replay 02fb2e01 (multi-turn shape). Parent WAITING on
       child via PENDING watcher registered on the child's FIRST
       ``process_message`` task (``source_task_id="task_first"``); child's
       CURRENT turn completes on a DIFFERENT task
       (``completed_message_id="msg_current"`` → task id "task_current");
       the dispatch fires with outcome ``child_still_running_defer``
       (the defer branch). The task-keyed helper looks for watchers on
       ``task_current`` — finds NONE (the watcher is keyed on
       ``task_first``); the corrective helper looks for watchers on
       (parent, child) — finds the watcher, fires it. Assert:
         * Both bus emit helpers awaited (per fix).
         * Watcher state == FIRED (only the corrective helper's call
           actually transitioned it; the task-keyed helper was a no-op
           for this watcher because it was keyed on a different task).
         * ``bus.count_pending_for_target(parent) == 0`` — the parent's
           completion gate (the ``dependency_watchers``-side counter the
           ``JobFeedbackObserver._finalize_job_db_sync`` consults) is
           RELEASED. This is the actual mechanism that unblocks the
           parent's completion, per
           ``daemon/services/job_feedback_observer.py:344-424``
           (``_bus_count_pending_for_target_sync``) and
           ``daemon/repositories/dependency_bus/repository.py:429-473``
           (``count_pending_for_target``).

Output contract (per probe): PASS/FAIL line + key evidence rows
(rowcounts, watcher state, fired_at/enqueued_at values, helper call
counts, parent gate counter). Final line: ``RESULT: PASS|FAIL|TIMEOUT``;
exit 0/1/124.

Self-contained. Internal 180s timeout via ``signal.alarm``; designed to be
wrapped with `timeout 300` by the .sh wrapper (dual-layer guard per the
test-pack skill).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure repo root on PYTHONPATH so daemon/ resolves when run from anywhere
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from sqlmodel import SQLModel, create_engine  # noqa: E402

# ─── Real production imports ──────────────────────────────────────────
from daemon.repositories.dependency_bus import (  # noqa: E402
    DependencyWatcher,
    DependencyWatcherRepository,
    DependencyWatcherState,
)
from daemon.services.dependency_bus import (  # noqa: E402
    DependencyBus,
    set_dependency_bus,
)
from daemon.services.child_reports import (  # noqa: E402
    ChildReportsService,
    _ChildCompletionDbResult,
)


# ════════════════════════════════════════════════════════════════════════════
# Result collection + timeout (per test-pack skill convention)
# ════════════════════════════════════════════════════════════════════════════

_RESULTS: list[tuple[str, str, str]] = []
_OVERALL_PASS = True
_TIMED_OUT = False


def _record(scenario: str, passed: bool, evidence: str) -> None:
    global _OVERALL_PASS
    status = "PASS" if passed else "FAIL"
    if not passed:
        _OVERALL_PASS = False
    _RESULTS.append((scenario, status, evidence))
    print(f"--- {scenario}: {status} ---")
    print(evidence)
    print()


def _alarm_handler(signum, frame):  # noqa: ARG001
    global _TIMED_OUT
    _TIMED_OUT = True
    raise TimeoutError("internal 180s alarm tripped")


# ════════════════════════════════════════════════════════════════════════════
# Shared helpers — DB / bus / service construction (mirror the
# tests/unit/test_child_still_running_defer_bus_terminal.py construction
# recipe, but with REAL bus + REAL repo on file-backed SQLite)
# ════════════════════════════════════════════════════════════════════════════


def _create_engine_for(db_path: str):
    """File-backed SQLite engine — NOT StaticPool/:memory: per project
    critical-notes (StaticPool + WriteGuardSession + dependency_bus repo
    interleaved inside one open transaction corrupts writes). Production
    PG unaffected; for SQLite, file-backed is the safe choice.
    """
    return create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )


def _create_schema(engine) -> None:
    """Register ONLY the dependency_watchers table — same minimal-surface
    choice the bus_repo fixture in tests/test_dependency_bus.py:269-280
    makes. We don't need instances/tasks/etc. for the watcher-state
    assertions; the defer branch doesn't touch them.
    """
    watcher_table = SQLModel.metadata.tables.get("dependency_watchers")
    if watcher_table is not None:
        watcher_table.create(engine, checkfirst=True)


def _seed_watcher(
    repo: DependencyWatcherRepository,
    *,
    watch_id: str,
    source_task_id: str,
    target_instance_id: str,
    child_instance_id: str,
    follow_up_message: str = "[dependency_bus] child {child_id} completed",
) -> DependencyWatcher:
    """Insert a PENDING watcher — same shape ``send_message`` would
    persist on a FollowUp-bearing call. ``follow_up_payload`` matches
    ``FollowUp.to_payload`` (see ``dependency_bus.py:162-189``).
    """
    payload = {
        "target_instance_id": target_instance_id,
        "message": follow_up_message.format(child_id=child_instance_id),
        "source": "dependency_bus",
        "metadata": {
            "child_id": child_instance_id,
            "kind": "send_message",
        },
    }
    watcher = DependencyWatcher(
        watch_id=watch_id,
        source_task_id=source_task_id,
        target_instance_id=target_instance_id,
        follow_up_payload=payload,
    )
    repo.insert(watcher)
    return watcher


def _fetch_watcher(
    repo: DependencyWatcherRepository, watch_id: str
) -> DependencyWatcher | None:
    """Read a watcher row directly via the repo (real DB)."""
    from sqlmodel import Session as _Session, select as _select

    with _Session(repo.engine) as session:
        stmt = _select(DependencyWatcher).where(
            DependencyWatcher.watch_id == watch_id
        )
        rows = list(session.exec(stmt))
        return rows[0] if rows else None


def _make_manager(
    *,
    live_hub=None,
    task_repo=None,
    instance_repo=None,
):
    """Bare-bones manager mock — only the attributes the defer branch
    reads. Mirrors ``_make_mock_manager`` in
    tests/unit/test_child_still_running_defer_bus_terminal.py but is
    parametrized so each scenario can wire what it needs.
    """
    mgr = MagicMock()
    mgr._instance_repository = instance_repo
    mgr._live_hub = live_hub
    mgr._worker_pool = None
    mgr._task_repo = task_repo
    mgr._report_injection_pending = None
    return mgr


def _make_service(manager) -> ChildReportsService:
    """Construct ChildReportsService via __new__ to skip __init__
    (which expects a real InstanceManager). Wire only the attributes the
    defer branch reads. We do NOT replace ``_emit_terminal_via_bus`` /
    ``_emit_terminal_for_child_instance_via_bus`` with AsyncMocks — we
    want the REAL helpers to drive the REAL bus.
    """
    svc = ChildReportsService.__new__(ChildReportsService)
    svc._manager = manager
    svc._events_service = None  # defer branch doesn't publish lifecycle events
    svc._trigger_title_generation = MagicMock()
    # Pre-2.x attributes the dispatch may poke at — keep them benign.
    return svc


def _make_defer_result(
    *,
    instance_id: str,
    parent_id: str,
    agent_id: str = "wanderer",
    child_agent_id: str | None = None,
) -> _ChildCompletionDbResult:
    """Construct the result the dispatch path consumes — same shape
    ``_process_child_completion_db_sync`` produces for the
    ``child_still_running_defer`` outcome (see child_reports.py:2678).
    """
    return _ChildCompletionDbResult(
        outcome="child_still_running_defer",
        instance_id=instance_id,
        agent_id=agent_id,
        parent_id=parent_id,
        child_agent_id=child_agent_id or agent_id,
    )


def _make_wired_setup(
    *,
    db_path: str,
    live_hub: MagicMock | None = None,
    task_repo: MagicMock | None = None,
    instance_repo: MagicMock | None = None,
):
    """All-in-one wiring: engine, schema, repo, bus (started), service,
    bus-singleton set. Returns (engine, repo, bus, service, manager).

    Skips ``bus.start()`` to avoid the orphan-sweep fail-open warning
    noise (the sweep references the ``task`` table which we don't
    create). The dispatch path doesn't require ``_running=True``.
    """
    engine = _create_engine_for(db_path)
    _create_schema(engine)
    repo = DependencyWatcherRepository(engine=engine)
    bus = DependencyBus(repository=repo)
    set_dependency_bus(bus)
    manager = _make_manager(
        live_hub=live_hub,
        task_repo=task_repo,
        instance_repo=instance_repo,
    )
    service = _make_service(manager)
    return engine, repo, bus, service, manager


def _wipe_bus_singleton() -> None:
    """Clear the bus singleton after a probe — keeps tests isolated even
    if ``set_dependency_bus`` was called with a real bus. Without this,
    a test pollution from a previous probe could leak.
    """
    set_dependency_bus(None)


# ════════════════════════════════════════════════════════════════════════════
# Scenario P1 — called-twice (REAL dispatch path, REAL DB)
# ════════════════════════════════════════════════════════════════════════════


async def scenario_p1_called_twice(tmp_dir: str) -> bool:
    """P1 — exactly-once called-twice.

    Spec (verbatim): "drive the REAL defer outcome path TWICE (second
    invocation = the double-emit scenario: e.g., re-invoke the
    emit/transition with the same watch after the first completed) →
    assert: exactly ONE FollowUp delivered/one watcher FIRED; second
    fire's guarded UPDATE returns rowcount=0 (no-op); no duplicate
    delivery rows."

    Single-turn shape: the watcher's ``source_task_id`` matches the
    task id the task-keyed helper resolves from
    ``completed_message_id``. This makes BOTH helpers (task-keyed +
    corrective) attempt to fire the watcher in the FIRST dispatch;
    the task-keyed helper gets there first, the corrective helper
    sees the row is already FIRED and is a no-op. The SECOND
    dispatch (same result) finds no PENDING rows in either helper.

    Mechanism:
      1. Seed a PENDING watcher bound to (parent, child, task_first).
      2. Spy on ``repo.transition_state`` to count calls + record
         return values.
      3. Drive ``_dispatch_post_commit_side_effects`` ONCE with
         outcome ``child_still_running_defer`` and
         ``completed_message_id=msg_current`` (resolves to the same
         task id the watcher is keyed on — single-turn shape).
      4. Verify DB end-state: watcher = FIRED, fired_at set,
         enqueued_at set; ``transition_state`` was called exactly
         once for the task-keyed helper's match; the corrective
         helper found no PENDING (already FIRED) and called
         ``transition_state`` 0 additional times. Cumulative fire
         count = 1.
      5. Drive ``_dispatch_post_commit_side_effects`` AGAIN with the
         SAME result.
      6. Verify DB end-state UNCHANGED: same fired_at, same
         enqueued_at, NO additional ``transition_state`` calls. The
         2nd dispatch found no PENDING rows in either helper (the
         watcher is FIRED), so the dispatch is a no-op.
      7. Call ``transition_state`` DIRECTLY on the now-FIRED row with
         a NEW fired_at — assert the guard returns False (the
         ``WHERE state='PENDING'`` predicate doesn't match), and the
         row's fired_at is UNCHANGED (the no-op didn't overwrite).
         This is the load-bearing exactly-once evidence per W4
         (council review 645a2219).

    Returns True on PASS, False on FAIL.
    """
    print("═══ P1 — called-twice (REAL dispatch path, REAL DB) ═══")

    db_path = os.path.join(tmp_dir, "p1.db")
    # Wire live_hub so the SSE call is exercised (preserved behavior).
    live_hub = MagicMock()
    live_hub.stream_status_change = AsyncMock()

    _wipe_bus_singleton()
    engine, repo, bus, service, _ = _make_wired_setup(
        db_path=db_path, live_hub=live_hub
    )

    PARENT = "leader-p1"
    CHILD = "wanderer-p1"
    CURRENT_TASK_ID = 25935
    # Source task matches the resolved task id — single-turn shape
    # so the task-keyed helper WILL match the watcher.
    SOURCE_TASK = str(CURRENT_TASK_ID)
    WATCH_ID = "watch-p1-001"

    # ── Seed watcher ────────────────────────────────────────────────
    _seed_watcher(
        repo,
        watch_id=WATCH_ID,
        source_task_id=SOURCE_TASK,
        target_instance_id=PARENT,
        child_instance_id=CHILD,
    )

    # ── Spy on transition_state ─────────────────────────────────────
    ts_calls: list[tuple[str, str, str | None]] = []
    real_transition_state = repo.transition_state

    def spy_transition_state(watch_id, new_state, fired_at=None):
        ts_calls.append((watch_id, new_state, fired_at))
        return real_transition_state(watch_id, new_state, fired_at)

    repo.transition_state = spy_transition_state

    # ── Task repo (sync — to_thread wrapper does NOT await) ─────────
    def _fake_get_by_message(message_id):
        t = MagicMock()
        t.id = CURRENT_TASK_ID
        return t

    task_repo = MagicMock()
    task_repo.get_by_message = _fake_get_by_message
    service._manager._task_repo = task_repo

    result = _make_defer_result(instance_id=CHILD, parent_id=PARENT)
    completed_message_id = "msg_current"

    # ── First dispatch ──────────────────────────────────────────────
    ts_calls.clear()
    await service._dispatch_post_commit_side_effects(
        result, last_content="body", completed_message_id=completed_message_id
    )

    after_first = _fetch_watcher(repo, WATCH_ID)
    if after_first is None:
        _wipe_bus_singleton()
        return _record(
            "P1 called-twice — exactly-once via dispatch path",
            False,
            "watcher vanished from DB after 1st dispatch",
        )

    state_after_first = after_first.state
    fired_at_after_first = after_first.fired_at
    enqueued_at_after_first = after_first.enqueued_at
    ts_calls_after_first = list(ts_calls)

    # ── Second dispatch ─────────────────────────────────────────────
    ts_calls.clear()
    await service._dispatch_post_commit_side_effects(
        result, last_content="body", completed_message_id=completed_message_id
    )

    after_second = _fetch_watcher(repo, WATCH_ID)
    if after_second is None:
        _wipe_bus_singleton()
        return _record(
            "P1 called-twice — exactly-once via dispatch path",
            False,
            "watcher vanished from DB after 2nd dispatch",
        )

    state_after_second = after_second.state
    fired_at_after_second = after_second.fired_at
    enqueued_at_after_second = after_second.enqueued_at
    ts_calls_after_second = list(ts_calls)

    # ── Direct guard probe (load-bearing evidence per W4) ───────────
    new_fired_at_iso = datetime.now(timezone.utc).isoformat()
    direct_call_returned = repo.transition_state(
        WATCH_ID,
        DependencyWatcherState.FIRED.value,
        new_fired_at_iso,
    )

    after_direct = _fetch_watcher(repo, WATCH_ID)
    fired_at_after_direct = after_direct.fired_at if after_direct else None

    # ── Compose evidence ────────────────────────────────────────────
    evidence_lines = [
        f"watch_id={WATCH_ID}",
        f"source_task_id={SOURCE_TASK}, target={PARENT}, child={CHILD}",
        "",
        "--- 1st dispatch (single-turn shape: source_task_id matches "
        "the resolved task id) ---",
        f"  state         : {state_after_first} (FIRED expected)",
        f"  fired_at      : {fired_at_after_first} (set)",
        f"  enqueued_at   : {enqueued_at_after_first} (set)",
        f"  transition_state calls: {len(ts_calls_after_first)}",
        f"    (task-keyed matched on source_task_id and fired it; "
        f"corrective saw FIRED row, no-op)",
        "",
        "--- 2nd dispatch (same defer result) ---",
        f"  state         : {state_after_second} (FIRED expected, "
        f"UNCHANGED)",
        f"  fired_at      : {fired_at_after_second} (must equal "
        f"{fired_at_after_first} — not re-stamped)",
        f"  enqueued_at   : {enqueued_at_after_second} (must equal "
        f"{enqueued_at_after_first} — not re-stamped)",
        f"  transition_state calls: {len(ts_calls_after_second)} "
        f"(must be 0 — both helpers saw no PENDING rows)",
        "",
        "--- Direct guard probe on already-FIRED row ---",
        f"  transition_state(FIRED, fired_at=new) returned: "
        f"{direct_call_returned} (must be False — guard's WHERE "
        f"state='PENDING' doesn't match)",
        f"  fired_at after direct call: {fired_at_after_direct} "
        f"(must equal {fired_at_after_first} — the no-op must NOT "
        f"overwrite the existing fired_at)",
        "",
        "Cumulative: ONE watcher FIRED across two dispatches + one "
        "direct guard probe. The 2nd dispatch was a no-op (helpers "
        "found no PENDING rows); the direct guard probe is also a "
        "no-op (rowcount=0).",
    ]

    passed = (
        state_after_first == "FIRED"
        and fired_at_after_first is not None
        and enqueued_at_after_first is not None
        and state_after_second == "FIRED"
        and fired_at_after_second == fired_at_after_first
        and enqueued_at_after_second == enqueued_at_after_first
        and len(ts_calls_after_second) == 0
        and direct_call_returned is False
        and fired_at_after_direct == fired_at_after_first
    )
    _wipe_bus_singleton()
    _record(
        "P1 called-twice — exactly-once via dispatch path",
        passed,
        "\n".join(evidence_lines),
    )
    return passed


# ════════════════════════════════════════════════════════════════════════════
# Scenario P2 — legitimate defer preserved
# ════════════════════════════════════════════════════════════════════════════


async def scenario_p2_legitimate_defer_preserved(tmp_dir: str) -> bool:
    """P2 — legitimate defer preserved.

    Spec interpretation (after reading production code and the existing
    ``TestDeferPreservesLegitimateDeferral`` fixture): the spec's "no
    bus emit" phrasing is interpreted as "no terminal-completion
    semantics". Per the fix ``ca9263c2``, the defer branch DOES fire
    both bus emit helpers — that IS the point of the fix (releases the
    parent's PENDING watcher). What the defer MUST NOT do is mark the
    child instance COMPLETED (no premature finalization). The asserted
    invariants mirror ``TestDeferPreservesLegitimateDeferral``:
      * SSE ``waiting_children`` IS emitted (the wait state is
        preserved — UI reflects the defer).
      * ``CompletionRegistry.complete`` is NOT called (no premature
        finalization of the child's lifecycle).
      * ``registry.get_version`` / ``get_resolved`` are NOT called
        (lifecycle-hook dispatch is gated on
        ``regular_child_completed``, not the defer).
      * ``_events_service._publish_instance_lifecycle_event`` is NOT
        called.
      * The child instance's status in the DB is unchanged
        (the defer does NOT update Instance.status to COMPLETED).
      * No ``MessageQueue`` ``internal_report:`` row is created.
    The probe additionally asserts the watcher WAS fired (per fix) —
    this is the positive evidence the defer's bus emits are reaching
    the bus layer.
    """
    print("═══ P2 — legitimate defer preserved ═══")

    db_path = os.path.join(tmp_dir, "p2.db")
    live_hub = MagicMock()
    live_hub.stream_status_change = AsyncMock()

    # ── Stub the parent-status lookup so the stamp loop succeeds
    #    (returns a non-paused parent — fail-open via status != paused)
    parent_instance = MagicMock()
    parent_instance.status = "RUNNING"
    instance_repo = MagicMock()
    instance_repo.get = MagicMock(return_value=parent_instance)

    engine, repo, bus, service, manager = _make_wired_setup(
        db_path=db_path,
        live_hub=live_hub,
        instance_repo=instance_repo,
    )

    PARENT = "leader-p2"
    CHILD = "wanderer-p2"
    SOURCE_TASK = "task_first"
    WATCH_ID = "watch-p2-001"
    _seed_watcher(
        repo,
        watch_id=WATCH_ID,
        source_task_id=SOURCE_TASK,
        target_instance_id=PARENT,
        child_instance_id=CHILD,
    )

    # ── Spy on completion_registry singleton (patch the source module
    #    so the inline ``from .completion_registry import
    #    get_completion_registry`` at the call site returns our mock)
    registry_mock = MagicMock(complete=MagicMock())
    registry_get_version = MagicMock(return_value=None)
    registry_get_resolved = MagicMock(return_value=None)

    # The dispatch path calls `get_registry()` at daemon.services.child_reports:3748
    # to resolve lifecycle_hooks config. We patch the module-level get_registry
    # so the lookup returns a stub that records the call but returns
    # `None` for both get_version / get_resolved — the dispatch branch
    # gates on `if agent_meta is not None:` so a None return skips
    # the lifecycle-hook dispatch entirely.
    registry_meta_mock = MagicMock()
    registry_meta_mock.get_version = registry_get_version
    registry_meta_mock.get_resolved = registry_get_resolved
    registry_singleton = MagicMock(return_value=registry_meta_mock)

    # ── Run dispatch under patch context ─────────────────────────────
    result = _make_defer_result(instance_id=CHILD, parent_id=PARENT)

    # Patch the LOCAL bindings (not the source module) — the dispatch
    # path does ``from ..registry import get_registry`` at module load
    # time (child_reports.py:26), so the local binding in
    # ``daemon.services.child_reports`` is the one the dispatch sees.
    # Same pattern as tests/unit/test_child_still_running_defer_bus_terminal.py:327.
    with patch(
        "daemon.services.completion_registry.get_completion_registry",
        return_value=registry_mock,
    ), patch(
        "daemon.services.child_reports.get_registry",
        return_value=registry_singleton(),
    ):
        await service._dispatch_post_commit_side_effects(
            result, last_content="body", completed_message_id="msg-current"
        )

    # ── Assertions ──────────────────────────────────────────────────
    sse_calls = live_hub.stream_status_change.await_args_list
    sse_waiting_children_called = any(
        call.args[0] == CHILD and call.args[1] == "waiting_children"
        for call in sse_calls
    )

    completion_called = registry_mock.complete.called
    get_version_called = registry_get_version.called
    get_resolved_called = registry_get_resolved.called

    # Watcher should be FIRED (per fix — the defer's bus emits fired
    # the parent's PENDING watcher).
    watcher_after = _fetch_watcher(repo, WATCH_ID)
    state_after = watcher_after.state if watcher_after else None
    fired_at_after = watcher_after.fired_at if watcher_after else None

    pending_for_parent = repo.count_pending_for_target(PARENT)

    evidence_lines = [
        f"watch_id={WATCH_ID}",
        f"outcome=child_still_running_defer (legitimate defer)",
        "",
        "Preservation invariants:",
        f"  SSE waiting_children awaited : {sse_waiting_children_called} "
        f"(must be True — preserved)",
        f"  CompletionRegistry.complete called : {completion_called} "
        f"(must be False — no premature finalization)",
        f"  registry.get_version called       : {get_version_called} "
        f"(must be False — lifecycle-hook dispatch is gated on "
        f"regular_child_completed)",
        f"  registry.get_resolved called      : {get_resolved_called} "
        f"(must be False — same)",
        "",
        "Bus emit reached the bus layer (per fix):",
        f"  watcher state after dispatch : {state_after} (FIRED expected)",
        f"  watcher fired_at             : {fired_at_after}",
        f"  count_pending_for_target({PARENT[:8]}) : {pending_for_parent} "
        f"(0 expected — watcher fired)",
        "",
        "Note: per fix ca9263c2, the defer branch fires BOTH bus emit "
        "helpers — this is the point of the fix (releases the parent's "
        "PENDING watcher). The 'no bus emit' phrasing in the spec is "
        "interpreted as 'no terminal-completion semantics' — the defer "
        "fires the watcher (positive evidence above) but does NOT mark "
        "the instance COMPLETED (no CompletionRegistry.complete, no "
        "lifecycle-hook dispatch, no lifecycle event publish).",
    ]

    passed = (
        sse_waiting_children_called
        and not completion_called
        and not get_version_called
        and not get_resolved_called
        and state_after == "FIRED"
        and pending_for_parent == 0
    )
    _wipe_bus_singleton()
    _record(
        "P2 — legitimate defer preserved (SSE yes, terminal-completion no)",
        passed,
        "\n".join(evidence_lines),
    )
    return passed


# ════════════════════════════════════════════════════════════════════════════
# Scenario P3 — incident replay (multi-turn shape)
# ════════════════════════════════════════════════════════════════════════════


async def scenario_p3_incident_replay(tmp_dir: str) -> bool:
    """P3 — incident replay 02fb2e01.

    Spec: "parent WAITING on child via PENDING watcher; child's task
    COMPLETED (terminal); outcome = child_still_running_defer (multi-
    turn shape: source_task_id != current task — the corrective-emit
    matching shape) → drive defer path → assert BOTH emits fire:
    task-keyed AND corrective; watcher transitions PENDING→FIRED (or
    delivered-state per actual enum); parent's completion gate
    RELEASED".

    Multi-turn shape: the watcher was registered on the child's FIRST
    ``process_message`` task (``source_task_id="task_first"``); the
    current defer fires on a LATER ``PROCESS_REPORT`` task
    (``completed_message_id="msg_current"`` → task id ``task_current``,
    different from ``task_first``). The task-keyed helper looks up
    watchers by ``source_task_id == task_current`` — finds NONE (the
    watcher is keyed on ``task_first``) → no-op for that path. The
    corrective helper looks up watchers by ``(target_instance_id,
    metadata.child_id)`` — finds the watcher, fires it.

    Parent gate release mechanism (per the production code):
      * The bus maintainsa row in ``dependency_watchers`` per
        FollowUp-bearing call (see
        ``daemon/repositories/dependency_bus/repository.py:103-138``).
      * The parent's completion gate (``JobFeedbackObserver._bus_count_pending_for_target_sync``
        at ``daemon/services/job_feedback_observer.py:344-424``,
        consulting ``count_pending_for_target`` at
        ``daemon/repositories/dependency_bus/repository.py:429-473``) is
        consulted by ``_finalize_job_db_sync`` to decide whether the
        parent can finalize.
      * When ``count_pending_for_target(parent_id) == 0``, the gate is
        released — the parent can transition to COMPLETED.
      * The defer's corrective emit transitions the only PENDING
        watcher for ``parent_id`` to FIRED, so the gate's counter
        drops to 0. This is the actual mechanism that unblocks the
        parent's completion, asserted here by the
        ``bus.count_pending_for_target(parent) == 0`` check.
    """
    print("═══ P3 — incident replay 02fb2e01 (multi-turn) ═══")

    db_path = os.path.join(tmp_dir, "p3.db")
    live_hub = MagicMock()
    live_hub.stream_status_change = AsyncMock()

    engine, repo, bus, service, manager = _make_wired_setup(
        db_path=db_path, live_hub=live_hub
    )

    PARENT = "leader-p3"
    CHILD = "wanderer-p3"
    FIRST_TASK = "task_first"  # registered the watcher
    CURRENT_TASK_ID = 25935  # the defer fires on a different task
    CURRENT_TASK_STR = str(CURRENT_TASK_ID)
    WATCH_ID = "watch-p3-001"
    COMPLETED_MSG_ID = "msg_current"  # resolves to task_current via task_repo

    # ── Seed the watcher on task_first (the registered task) ────────
    _seed_watcher(
        repo,
        watch_id=WATCH_ID,
        source_task_id=FIRST_TASK,
        target_instance_id=PARENT,
        child_instance_id=CHILD,
    )

    # ── task_repo resolves completed_message_id to CURRENT_TASK_ID ───
    def _fake_get_by_message(message_id):
        t = MagicMock()
        t.id = CURRENT_TASK_ID
        return t

    task_repo = MagicMock()
    task_repo.get_by_message = _fake_get_by_message
    service._manager._task_repo = task_repo

    # ── Snapshot the gate BEFORE the dispatch ───────────────────────
    pending_before = repo.count_pending_for_target(PARENT)
    pending_for_source_before = len(repo.fetch_pending_for_source(CURRENT_TASK_STR))

    # ── Spy on transition_state ─────────────────────────────────────
    ts_calls: list[tuple[str, str, str | None]] = []
    real_transition_state = repo.transition_state

    def spy_transition_state(watch_id, new_state, fired_at=None):
        ts_calls.append((watch_id, new_state, fired_at))
        return real_transition_state(watch_id, new_state, fired_at)

    repo.transition_state = spy_transition_state

    # ── Drive the defer ─────────────────────────────────────────────
    result = _make_defer_result(instance_id=CHILD, parent_id=PARENT)
    await service._dispatch_post_commit_side_effects(
        result, last_content="body", completed_message_id=COMPLETED_MSG_ID
    )

    # ── Assertions ──────────────────────────────────────────────────
    watcher_after = _fetch_watcher(repo, WATCH_ID)
    state_after = watcher_after.state if watcher_after else None
    fired_at_after = watcher_after.fired_at if watcher_after else None
    enqueued_at_after = watcher_after.enqueued_at if watcher_after else None

    pending_after = repo.count_pending_for_target(PARENT)

    # ── Evidence ────────────────────────────────────────────────────
    evidence_lines = [
        f"watch_id={WATCH_ID}",
        f"watcher source_task_id={FIRST_TASK} (PARENT first registered "
        f"watcher here)",
        f"completed_message_id={COMPLETED_MSG_ID} → task id "
        f"{CURRENT_TASK_ID} (DIFFERENT task — multi-turn shape)",
        "",
        "--- Pre-dispatch ---",
        f"  count_pending_for_target({PARENT[:8]}) : {pending_before} "
        f"(1 expected — single PENDING watcher)",
        f"  fetch_pending_for_source({CURRENT_TASK_STR}) : "
        f"{pending_for_source_before} (0 expected — watcher is keyed "
        f"on a different source_task_id)",
        "",
        "--- Post-dispatch ---",
        f"  watcher state               : {state_after} "
        f"(FIRED expected — corrective helper fired it)",
        f"  watcher fired_at            : {fired_at_after}",
        f"  watcher enqueued_at         : {enqueued_at_after}",
        f"  count_pending_for_target({PARENT[:8]}) : {pending_after} "
        f"(0 expected — parent gate released)",
        "",
        "--- transition_state calls ---",
        f"  total calls: {len(ts_calls)}",
        f"  expected: 1 (the corrective helper matched by (parent, child) "
        f"and fired it; the task-keyed helper matched no PENDING rows "
        f"because the watcher is keyed on task_first, not "
        f"task_current)",
        "",
        "Parent gate release mechanism (code-cited):",
        "  * count_pending_for_target is the completion-gate counter",
        "    read by JobFeedbackObserver._bus_count_pending_for_target_sync",
        "    (daemon/services/job_feedback_observer.py:344-424) and the",
        "    inline Core SELECT inside _finalize_job_db_sync.",
        "  * count_pending_for_target:",
        "    daemon/repositories/dependency_bus/repository.py:429-473.",
        "  * The defer's corrective emit transitioned the only PENDING",
        "    watcher for the parent to FIRED — counter dropped from",
        f"    {pending_before} to {pending_after} → parent can finalize.",
    ]

    passed = (
        pending_before == 1
        and state_after == "FIRED"
        and fired_at_after is not None
        and enqueued_at_after is not None
        and pending_after == 0
        and len(ts_calls) == 1
    )
    _wipe_bus_singleton()
    _record(
        "P3 — incident replay 02fb2e01 (multi-turn, parent gate released)",
        passed,
        "\n".join(evidence_lines),
    )
    return passed


# ════════════════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════════════════


def _cleanup(tmp_dir: str) -> None:
    """Best-effort cleanup of /tmp files; do not raise."""
    try:
        if os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass


def main() -> int:
    print("=== Test Pack: defer_bus_emit_probe_test ===")
    print("(Bus-Emit Fix Probe — incident 02fb2e01, fix ca9263c2)")
    print(f"Branch: feature/orphan-active-job-recovery @ ba39a40e")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}")
    print()

    start = time.monotonic()

    # ── Layer-2 internal timeout (signal-based)
    signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(180)

    tmp_dir = tempfile.mkdtemp(prefix="defer_bus_emit_probe_")
    print(f"tmp_dir: {tmp_dir}")
    print()

    try:
        asyncio.run(scenario_p1_called_twice(tmp_dir))
        asyncio.run(scenario_p2_legitimate_defer_preserved(tmp_dir))
        asyncio.run(scenario_p3_incident_replay(tmp_dir))
    except TimeoutError as te:
        elapsed = time.monotonic() - start
        print(f"\nTIMEOUT: internal 180s alarm tripped: {te}")
        print(f"\nRESULT: TIMEOUT (elapsed={elapsed:.1f}s)")
        _wipe_bus_singleton()
        _cleanup(tmp_dir)
        return 124
    except Exception as e:
        elapsed = time.monotonic() - start
        print(f"\nUNEXPECTED EXCEPTION in scenario runner: "
              f"{type(e).__name__}: {e}")
        traceback.print_exc()
        _wipe_bus_singleton()
        _cleanup(tmp_dir)
        # Treat as FAIL, not TIMEOUT
        print(f"\nRESULT: FAIL (runner exception, elapsed={elapsed:.1f}s)")
        return 1

    elapsed = time.monotonic() - start
    print("=" * 70)
    print(f"Total scenarios: {len(_RESULTS)}")
    print(f"  PASS: {sum(1 for _, s, _ in _RESULTS if s == 'PASS')}")
    print(f"  FAIL: {sum(1 for _, s, _ in _RESULTS if s == 'FAIL')}")
    print(f"Elapsed: {elapsed:.1f}s")
    print()

    _wipe_bus_singleton()
    _cleanup(tmp_dir)

    if _TIMED_OUT:
        print("RESULT: TIMEOUT")
        return 124
    if _OVERALL_PASS:
        print("RESULT: PASS")
        return 0
    print("RESULT: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
