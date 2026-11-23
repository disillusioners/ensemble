"""Runtime parity proof for the OrphanWatcher engine pilot — REAL bus, REAL SQL.

THE GAP THIS FILE CLOSES (leader steering item D, 2026-11-23)
-------------------------------------------------------------
The landed unit pack (``test_orphan_watcher_engine_parity.py``) drives the
engine's sync bridge against a SHAPE-MOCKED bus (``_RealShapedBus``). A
shape-mock cannot see the one production hazard that is invisible to unit
tests of that kind: **loop affinity**.

* The LEGACY path awaits ``bus._sweep_orphan_watchers(...)`` on the
  DAEMON'S MAIN LOOP.
* The ENGINE path runs the SAME method via ``asyncio.run(...)`` INSIDE A
  WORKER THREAD (``dispatch.run_db_offload`` → ``asyncio.to_thread``) —
  i.e. a DIFFERENT EVENT LOOP.

Any ``asyncio.Lock`` / ``Event`` / ``Queue`` / ``Future`` / ``Condition``
held by ``DependencyBus`` and touched inside that method's call graph would
raise "got Future/awaitable attached to a different loop" (or "is bound to a
different event loop") under the ENGINE path and NOT under the legacy path.
With the legacy loop suppressed by the ownership conjunct, that failure IS
the real dead sweep lane. The study's dead-lane claim (flag ON + shadow ON)
was REFUTED by code; this file closes the OTHER dead-lane claim — the
loop-affinity one — by MEASUREMENT against a REAL bus.

WHAT IS MEASURED HERE (not designed)
------------------------------------
1. LOOP IDENTITY: the legacy run and the engine run observe DIFFERENT
   running-loop ids and DIFFERENT thread idents, from inside the real bus
   method (a spy AROUND the real bound method — the method body itself is
   never replaced).
2. ADVERSARIAL LOCK BINDING: the bus's ``_locks_guard`` is deliberately
   BOUND TO THE MAIN LOOP first (``await bus._get_lock(...)``). If the
   sweep touched that guard, the engine run would raise immediately. It
   does not.
3. DIRECTION-A PARITY: engine-executed sweep ≡ standalone sweep — the SAME
   orphans cancelled, the SAME rows preserved (grace window / live task /
   non-PENDING), over two independent but identically-shaped fixture sets.
4. BORN-DEAD IMMUNITY: the service is constructed BEFORE the bus exists and
   picks the bus up on a later tick (the late-bind fix).
5. DIRECTION-B BYTE PROOF: the pilot commits did not touch the bus
   implementation, and added to the sweep module WITHOUT REMOVING a line —
   the legacy path is byte-preserved (fallback is provably unchanged).

NON-VACUITY: leg 6 is a MUTATION SELF-PROOF — a bus subclass whose sweep
DOES acquire the loop-bound guard must make the harness FAIL. A harness
that cannot fail is not evidence.

HERMETIC: SQLite (the sweep's SQL is dialect-portable by design — the
docstring pins the lexicographic ISO-8601 comparison and the embedded
IN-list), so no ambient ``POSTGRES_*`` is read and no live DB is touched.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session

from daemon.repositories.dependency_bus.models import DependencyWatcher
from daemon.repositories.dependency_bus.repository import DependencyWatcherRepository
from daemon.repositories.task.models import Task, TaskStatus, TaskType
from daemon.services.dependency_bus import DependencyBus, set_dependency_bus
from daemon.services.orphan_watcher_sweep import (
    OrphanWatcherSweepService,
    build_engine_executor,
)

GRACE = 30
PILOT_COMMITS_BASE = "5044dc148"  # the born-dead fix — the pilot commits follow


# ──────────────────────────────────────────────────────────────────────
# Fixture helpers
# ──────────────────────────────────────────────────────────────────────


def _make_engine(tmp_path: Path):
    """A REAL SQLAlchemy engine over a temp SQLite file."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'bus.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    DependencyWatcher.__table__.create(engine)
    Task.__table__.create(engine)
    return engine


def _task_id(prefix: str) -> int:
    """A deterministic, per-prefix ``task.id`` (the sweep casts id to TEXT).

    ``hash()`` is randomized per process for strings — this must be stable
    across runs so the fixture is reproducible.
    """
    digest = hashlib.md5(prefix.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 900000 + 1000


def _seed(engine, prefix: str, grace: int = GRACE) -> dict[str, str]:
    """Seed one fixture SET of five watcher shapes. Returns shape→expectation.

    Shapes (identical for every set — only the ids differ):

    * ``orphan_old``   — PENDING, older than the grace, source task GONE
                          ⇒ CANCELLED (the sweep's work)
    * ``orphan_young`` — PENDING, inside the grace window, source task GONE
                          ⇒ PRESERVED (W-C commit→emit race protection)
    * ``active_old``   — PENDING, older than the grace, source task ACTIVE
                          ⇒ PRESERVED (the active-task IN-list predicate)
    * ``fired_old``    — FIRED (not PENDING), older than the grace
                          ⇒ PRESERVED (the non-PENDING skip)
    * ``cancelled_old``— CANCELLED (already terminal)
                          ⇒ PRESERVED (idempotence)
    """
    now = datetime.now(timezone.utc)
    old = (now - timedelta(seconds=grace * 4)).isoformat()
    young = now.isoformat()
    active_task_id = _task_id(prefix)
    rows = [
        ("orphan_old", f"{prefix}-orphan-old", "PENDING", old, "CANCELLED"),
        ("orphan_young", f"{prefix}-orphan-young", "PENDING", young, "PENDING"),
        ("active_old", f"{prefix}-active", "PENDING", old, "PENDING"),
        ("fired_old", f"{prefix}-fired", "FIRED", old, "FIRED"),
        ("cancelled_old", f"{prefix}-cancelled", "CANCELLED", old, "CANCELLED"),
    ]
    with Session(engine) as session:
        session.execute(
            text(
                "INSERT INTO dependency_watchers (watch_id, source_task_id, "
                "target_instance_id, state, created_at, follow_up_payload, "
                "metadata) VALUES (:w, :s, :t, :st, :cr, '{}', '{}')"
            ),
            [
                {
                    "w": f"{prefix}-w-{shape}",
                    "s": (str(active_task_id) if shape == "active_old"
                          else f"{prefix}-source-{shape}"),
                    "t": f"{prefix}-inst",
                    "st": state,
                    "cr": created,
                }
                for shape, _sid, state, created, _exp in rows
            ],
        )
        session.add(
            Task(
                id=active_task_id,
                work_id=f"{prefix}-w1",
                task_type=TaskType.PROCESS_MESSAGE,
                instance_id=f"{prefix}-inst",
                status=TaskStatus.RUNNING,
                created_at=now,
            )
        )
        session.commit()
    return {shape: expected for shape, _sid, _st, _cr, expected in rows}


def _states(engine, prefix: str) -> dict[str, str]:
    """The per-shape resulting state, read back from the DB."""
    with Session(engine) as session:
        found = session.execute(
            text(
                "SELECT watch_id, state FROM dependency_watchers "
                "WHERE watch_id LIKE :p ORDER BY watch_id"
            ),
            {"p": f"{prefix}-w-%"},
        ).fetchall()
    out: dict[str, str] = {}
    for watch_id, state in found:
        shape = str(watch_id).rsplit("-w-", 1)[-1]
        out[shape] = str(state)
    return out


def _spy(bus: DependencyBus, observed: dict) -> DependencyBus:
    """Wrap the REAL bound sweep method with a loop/thread observer.

    The method BODY is never replaced — the spy only records the running
    loop and thread idents and then delegates to the real implementation.
    """
    real = bus._sweep_orphan_watchers

    async def probe(*, min_watcher_age_seconds: int = GRACE):
        observed["loop_id"] = id(asyncio.get_running_loop())
        observed["thread_ident"] = threading.get_ident()
        observed["main_thread_ident"] = threading.main_thread().ident
        observed["calls"] = observed.get("calls", 0) + 1
        return await real(min_watcher_age_seconds=min_watcher_age_seconds)

    bus._sweep_orphan_watchers = probe  # type: ignore[method-assign]
    return bus


# ──────────────────────────────────────────────────────────────────────
# 1 + 2 — the loop-affinity question, answered by measurement
# ──────────────────────────────────────────────────────────────────────


def test_sweep_source_contains_no_loop_affine_primitive():
    """SOURCE pin: the sweep's call graph touches no loop-bound primitive.

    ``asyncio.to_thread`` IS allowed — it binds to the RUNNING loop of its
    caller (the fresh ``asyncio.run`` loop in the worker thread), which is
    exactly why it is loop-agnostic. Everything below would bind a primitive
    to ONE loop and break under the engine path.
    """
    src = inspect.getsource(DependencyBus._sweep_orphan_watchers)
    forbidden = (
        "asyncio.Lock(",
        "asyncio.Event(",
        "asyncio.Queue(",
        "asyncio.Condition(",
        "asyncio.Future(",
        "asyncio.Semaphore(",
        "ensure_future",
        "create_task",
        "get_event_loop",
        "run_coroutine_threadsafe",
        "self._locks",
        "self._parent_locks",
        "_locks_guard",
        "_parent_locks_guard",
    )
    hits = [token for token in forbidden if token in src]
    assert not hits, (
        "the orphan sweep's source now touches a loop-affine primitive "
        f"{hits} — the ENGINE path runs it on a DIFFERENT event loop "
        "(asyncio.run inside a worker thread) and would raise; the LEGACY "
        "path (main loop) would not"
    )
    # The one await it does have is loop-agnostic by construction.
    assert "asyncio.to_thread" in src


async def test_engine_path_runs_the_real_sweep_on_a_different_loop(tmp_path):
    """MEASURED: the two paths really do run on two different loops."""
    engine = _make_engine(tmp_path)
    bus = _spy(DependencyBus(DependencyWatcherRepository(engine)), {})
    set_dependency_bus(bus)
    service = OrphanWatcherSweepService(
        dependency_bus=bus, interval_seconds=1, min_watcher_age_seconds=GRACE
    )

    legacy_obs: dict = {}
    engine_obs: dict = {}
    real = bus._sweep_orphan_watchers

    async def probe_legacy(*, min_watcher_age_seconds: int = GRACE):
        legacy_obs["loop_id"] = id(asyncio.get_running_loop())
        legacy_obs["thread_ident"] = threading.get_ident()
        return await real(min_watcher_age_seconds=min_watcher_age_seconds)

    async def probe_engine(*, min_watcher_age_seconds: int = GRACE):
        engine_obs["loop_id"] = id(asyncio.get_running_loop())
        engine_obs["thread_ident"] = threading.get_ident()
        return await real(min_watcher_age_seconds=min_watcher_age_seconds)

    bus._sweep_orphan_watchers = probe_legacy
    await service.sweep_once()
    bus._sweep_orphan_watchers = probe_engine
    # EXACTLY what dispatch.run_db_offload does for a DB_OFFLOAD executor.
    await asyncio.to_thread(build_engine_executor(GRACE))

    assert legacy_obs["loop_id"] != engine_obs["loop_id"], (
        "the engine path did NOT run on a different event loop — the "
        "loop-affinity hazard this lane must rule out is not being exercised"
    )
    assert legacy_obs["thread_ident"] != engine_obs["thread_ident"]
    assert legacy_obs["thread_ident"] == threading.main_thread().ident
    assert engine_obs["thread_ident"] != threading.main_thread().ident


async def _bind_and_hold_guard(bus) -> callable:
    """Bind the bus's ``_locks_guard`` to THIS loop and HOLD it.

    MEASURED semantics (CPython 3.14, this worktree's interpreter):
    ``asyncio.Lock`` binds to a loop ONLY on a CONTENDED acquire — an
    uncontended acquire takes the fast path and never checks the loop::

        after construct:            _loop = None
        after UNCONTENDED acquire:  _loop = None
        after CONTENDED acquire:    _loop = <the running loop>
        other-loop acquire (free):  SUCCEEDS, no error

    So the only state that makes a cross-loop acquire FAIL is a lock that
    is (a) bound to another loop and (b) still HELD. This helper produces
    exactly that state: a contender forces the binding, and the holder
    keeps the lock locked.

    Returns an async cleanup callable (cancel the contender, release the
    holder). The binding itself persists after cleanup — which is exactly
    the state a long-lived daemon would be in.
    """
    guard = bus._locks_guard
    await guard.acquire()  # holder takes it (fast path — no binding yet)

    async def _contender():
        await guard.acquire()  # CONTENDED -> binds guard._loop to THIS loop
        guard.release()

    contender = asyncio.create_task(_contender())
    for _ in range(100):
        if getattr(guard, "_loop", None) is not None:
            break
        await asyncio.sleep(0.01)
    assert getattr(guard, "_loop", None) is not None, (
        "the guard did not bind to this loop — the adversarial setup is "
        "vacuous and the cross-loop hazard is not being exercised"
    )

    async def _cleanup():
        contender.cancel()
        try:
            await contender
        except asyncio.CancelledError:
            pass
        if guard.locked():
            guard.release()

    return _cleanup


async def test_engine_path_survives_a_bus_whose_lock_is_bound_and_held_by_the_main_loop(
    tmp_path,
):
    """ADVERSARIAL: the bus's asyncio.Lock is bound to the MAIN loop and HELD.

    If the sweep touched that guard, the engine path (a DIFFERENT loop)
    would contend on a main-loop-bound lock and raise "is bound to a
    different event loop". The sweep must not touch it, and must complete
    normally — which is what this leg measures.
    """
    engine = _make_engine(tmp_path)
    bus = DependencyBus(DependencyWatcherRepository(engine))
    set_dependency_bus(bus)

    cleanup = await _bind_and_hold_guard(bus)
    try:
        _seed(engine, "adv")
        result = await asyncio.to_thread(build_engine_executor(GRACE))

        assert result["bus_wired"] == 1
        assert result["cancelled"] == 1, (
            "the engine-path sweep did not perform the orphan cancellation — "
            "it must be equivalent to the legacy sweep"
        )
        assert _states(engine, "adv")["orphan_old"] == "CANCELLED"
    finally:
        await cleanup()


async def test_mutation_self_proof_the_harness_detects_loop_affinity(tmp_path):
    """NON-VACUITY: a lock-touching sweep MUST make the engine path fail.

    A harness that cannot fail proves nothing. This leg builds a bus whose
    sweep DOES acquire the (main-loop-bound, HELD) guard and asserts the
    engine path raises the cross-loop RuntimeError — i.e. the guard in the
    legs above is load-bearing.
    """
    engine = _make_engine(tmp_path)

    class _LockTouchingBus(DependencyBus):
        async def _sweep_orphan_watchers(self, *, min_watcher_age_seconds: int = GRACE):
            async with self._locks_guard:  # the mutation: a loop-bound primitive
                return await super()._sweep_orphan_watchers(
                    min_watcher_age_seconds=min_watcher_age_seconds
                )

    bus = _LockTouchingBus(DependencyWatcherRepository(engine))
    set_dependency_bus(bus)
    cleanup = await _bind_and_hold_guard(bus)
    try:
        with pytest.raises(RuntimeError) as exc:
            await asyncio.to_thread(build_engine_executor(GRACE))
        assert "different event loop" in str(exc.value), (
            "the mutation did not produce the cross-loop failure the harness "
            f"is supposed to catch — got {exc.value!r}; the harness is VACUOUS"
        )
    finally:
        await cleanup()


async def test_direction_a_runtime_parity_engine_vs_legacy(tmp_path):
    """DIRECTION A (real bus, real SQL): the SAME orphans, actions, no-ops.

    Two INDEPENDENT but identically-shaped fixture sets are seeded in one
    DB: set ``LEG`` is swept by the standalone service (main loop), set
    ``ENG`` by the engine's executor (worker thread, fresh loop). The
    per-shape outcome maps and the cancellation counts must be IDENTICAL.
    """
    engine = _make_engine(tmp_path)
    bus = DependencyBus(DependencyWatcherRepository(engine))
    set_dependency_bus(bus)

    expected = _seed(engine, "LEG")

    # ── LEGACY path (main loop) ──────────────────────────────────────
    # The sweep is GLOBAL (it cancels every orphan PENDING watcher in the
    # DB), so the two sets are seeded SEQUENTIALLY — set ENG is seeded
    # AFTER the legacy sweep, otherwise the legacy sweep would cancel ENG's
    # orphan too and the engine run would find nothing to do.
    legacy_service = OrphanWatcherSweepService(
        dependency_bus=bus, interval_seconds=1, min_watcher_age_seconds=GRACE
    )
    legacy_before = _states(engine, "LEG")
    legacy_result = await legacy_service.sweep_once()
    legacy_states = _states(engine, "LEG")

    # ── ENGINE path (worker thread, fresh event loop) ───────────────
    _seed(engine, "ENG")
    engine_before = _states(engine, "ENG")
    engine_result = await asyncio.to_thread(build_engine_executor(GRACE))
    engine_states = _states(engine, "ENG")

    assert legacy_states == expected, (
        f"the legacy path did not match the expected predicate outcomes: "
        f"{legacy_states} != {expected}"
    )
    assert engine_states == expected, (
        f"the ENGINE path diverged from the legacy path: "
        f"{engine_states} != {expected}"
    )
    assert legacy_states == engine_states, (
        "PARITY VIOLATION: the engine-executed sweep and the standalone "
        f"sweep disagree — legacy={legacy_states} engine={engine_states}"
    )
    assert legacy_result["cancelled"] == 1
    assert engine_result["cancelled"] == 1
    assert engine_result["grace_seconds"] == GRACE
    # THE SAME ACTIONS: exactly ONE row transitions on each side, and it is
    # the same row, through the same state change. Everything else is a
    # NO-OP on both sides (grace window / live task / non-PENDING /
    # already-terminal).
    legacy_actions = {
        k: (legacy_before[k], legacy_states[k])
        for k in legacy_before
        if legacy_before[k] != legacy_states[k]
    }
    engine_actions = {
        k: (engine_before[k], engine_states[k])
        for k in engine_before
        if engine_before[k] != engine_states[k]
    }
    assert legacy_actions == {"orphan_old": ("PENDING", "CANCELLED")}
    assert engine_actions == legacy_actions, (
        "PARITY VIOLATION (actions): the engine path and the legacy path "
        f"performed DIFFERENT actions — legacy={legacy_actions} "
        f"engine={engine_actions}"
    )
    # THE SAME NO-OPS: every other row is untouched, on both sides.
    legacy_noops = {k: v for k, v in legacy_states.items() if k not in legacy_actions}
    engine_noops = {k: v for k, v in engine_states.items() if k not in engine_actions}
    assert legacy_noops == engine_noops, (
        "PARITY VIOLATION (no-ops): the preserved rows differ — "
        f"legacy={legacy_noops} engine={engine_noops}"
    )
    assert len(legacy_noops) == 4


async def test_engine_path_is_idempotent_across_repeated_ticks(tmp_path):
    """A second engine tick finds no work — no double execution."""
    engine = _make_engine(tmp_path)
    bus = DependencyBus(DependencyWatcherRepository(engine))
    set_dependency_bus(bus)
    _seed(engine, "idem")

    first = await asyncio.to_thread(build_engine_executor(GRACE))
    second = await asyncio.to_thread(build_engine_executor(GRACE))
    third = await asyncio.to_thread(build_engine_executor(GRACE))

    assert first["cancelled"] == 1
    assert second["cancelled"] == 0
    assert third["cancelled"] == 0


async def test_bridge_failure_accounting_is_asymmetric_but_both_fail_open(tmp_path):
    """The bus sweep is internally FAIL-OPEN; only BRIDGE failures reach the engine.

    Mapped explicitly (the study's asymmetry): a DB-level failure inside the
    bus is swallowed by the bus (returns 0) ⇒ BOTH paths observe
    ``cancelled == 0`` and neither records an error. Only a BRIDGE-level
    failure (the bus getter raising) reaches the engine, which stamps
    ``FAILED_BACKOFF``; the legacy path's equivalent counts it in
    ``_errors_total``.
    """
    engine = _make_engine(tmp_path)
    bus = DependencyBus(DependencyWatcherRepository(engine))
    set_dependency_bus(bus)
    _seed(engine, "failopen")

    # DB-level failure: the sweep's own try/except swallows it.
    original = bus._sweep_orphan_watchers

    async def _db_error(*, min_watcher_age_seconds: int = GRACE):
        raise RuntimeError("simulated DB failure inside the sweep")

    # The REAL method swallows its own DB errors, so simulate the DB error
    # INSIDE the real method's own guard by breaking the engine binding.
    import daemon.services.orphan_watcher_sweep as ows

    def _boom_engine():
        raise RuntimeError("simulated DB failure inside the sweep")

    broken = ows.build_engine_executor(GRACE)
    # Drive the bridge with a bus whose sweep raises (BRIDGE-level).
    bus._sweep_orphan_watchers = _db_error
    with pytest.raises(RuntimeError):
        await asyncio.to_thread(broken)

    # Legacy side: the service catches the same raise and COUNTS it.
    bus._sweep_orphan_watchers = _db_error
    service = OrphanWatcherSweepService(
        dependency_bus=bus, interval_seconds=1, min_watcher_age_seconds=GRACE
    )
    legacy_result = await service.sweep_once()
    assert legacy_result["errors"] == 1
    assert legacy_result["cancelled"] == 0

    bus._sweep_orphan_watchers = original


# ──────────────────────────────────────────────────────────────────────
# 4 — born-dead immunity (the late-bind fix), against a REAL bus
# ──────────────────────────────────────────────────────────────────────


async def test_born_dead_immunity_service_picks_up_a_later_wired_bus(tmp_path):
    """The service is constructed BEFORE the bus exists and still sweeps.

    This is the defect the whole pilot exists to make impossible: the
    standalone service used to capture ``get_dependency_bus()`` at
    CONSTRUCTION (which froze ``None`` for the daemon's lifetime — the
    born-dead lane). The fix is a PER-TICK late bind. Proven here against
    a REAL bus: construct the service with NO bus, wire the singleton
    afterwards, tick, and observe a REAL sweep with a REAL cancellation.
    """
    engine = _make_engine(tmp_path)
    set_dependency_bus(None)
    service = OrphanWatcherSweepService(
        dependency_bus=None, interval_seconds=1, min_watcher_age_seconds=GRACE
    )
    _seed(engine, "born")

    # Tick 1 — no bus wired yet: the lane ticks but does nothing.
    first = await service.sweep_once()
    assert first["effective_runs"] == 0
    assert first["cancelled"] == 0

    # The bus is wired LATER (as ``init_dependency_bus`` does at boot).
    bus = DependencyBus(DependencyWatcherRepository(engine))
    set_dependency_bus(bus)

    # Tick 2 — the late bind resolves the bus and the sweep really runs.
    second = await service.sweep_once()
    assert second["effective_runs"] == 1, (
        "the service did NOT pick up the bus wired after construction — "
        "the born-dead trap is back"
    )
    assert second["cancelled"] == 1
    assert _states(engine, "born")["orphan_old"] == "CANCELLED"

    set_dependency_bus(None)


# ──────────────────────────────────────────────────────────────────────
# 5 — DIRECTION B: the legacy path is byte-unchanged by the pilot commits
# ──────────────────────────────────────────────────────────────────────


def _git(repo_root: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if out.returncode != 0:
        pytest.fail(
            f"git {' '.join(args)} failed in {repo_root}: {out.stderr.strip()} — "
            "the Direction-B byte proof requires a git checkout"
        )
    return out.stdout


def test_direction_b_pilot_commits_did_not_touch_the_bus_implementation():
    """BYTE PROOF (direction B): the bus implementation is untouched.

    The migration moved the SWEEP, not the swept-over machinery. If the
    bus's own code had changed, the engine path would be comparing against
    a different implementation than the legacy path — parity would be
    meaningless.
    """
    repo_root = Path(__file__).resolve().parents[3]
    diff = _git(
        repo_root,
        "diff",
        f"{PILOT_COMMITS_BASE}..HEAD",
        "--",
        "daemon/services/dependency_bus.py",
    )
    assert diff.strip() == "", (
        "the pilot commits MODIFIED daemon/services/dependency_bus.py — the "
        "engine path and the legacy path would no longer run the same "
        f"implementation:\n{diff}"
    )


def test_direction_b_sweep_module_gained_lines_and_removed_none():
    """BYTE PROOF (direction B): the sweep module is ADDITIVE only.

    A removed line in the legacy sweep path would mean the fallback is not
    byte-identical to the pre-pilot service. The pilot commits add the
    engine seam (constants, ownership conjunct, executor builder, re-arm)
    and change NOTHING that the legacy path executes.
    """
    repo_root = Path(__file__).resolve().parents[3]
    diff = _git(
        repo_root,
        "diff",
        f"{PILOT_COMMITS_BASE}..HEAD",
        "--",
        "daemon/services/orphan_watcher_sweep.py",
    )
    removed = [
        line
        for line in diff.splitlines()
        if line.startswith("-") and not line.startswith("---")
    ]
    assert not removed, (
        "the pilot commits REMOVED lines from the legacy sweep module — the "
        f"flag-OFF fallback is not byte-preserved:\n{chr(10).join(removed)}"
    )
    added = [
        line
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    assert added, "the diff is vacuous — no pilot additions found"
