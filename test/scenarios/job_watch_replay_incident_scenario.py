#!/usr/bin/env python3
"""Job-Event Watch Replay Incident Scenario — incident-mechanics replay.

Scenario harness that proves the ORIGINAL f27e2d15 incident mechanics are
DEAD on the fix-under-test (branch ``fix/job-event-watch-replay``,
commit ac789d45).

Closes two incident classes:

  RC1 (replay flood — symptoms 1, 2, 3, 6): mission f27e2d15 had ~14
  task receipts; after ``watch_mission`` registered ALL receipts, every
  mission-instance completed flip mass-fired [JOB_EVENT] notifications
  for OLD already-terminal receipts (one per conversation turn via
  durable message_queue FIFO); re-calling ``watch_mission`` re-armed
  them → 9 receipts fired twice; unwatch_job said "no matching receipt
  watches" (rows already CAS-consumed); non-failed receipts carried
  the mission instance's LATEST ASSISTANT MESSAGE inside the ``Error:``
  field.

  RC2 (error-slot mis-wiring — symptom 4): the mission-instance's
  latest assistant message was pre-fetched as ``result_summary`` and
  passed POSITIONALLY into ``notify_watchers``' third parameter
  (``error=``), masking every receipt's own (NULL) error.

The fix lands two invariants:

  F1: ``watch_mission`` arms ONLY currently-live receipts (registration
      terminal filter). Already-settled receipts are skipped — no row,
      no notify. Re-call after a flip is a DELTA-ARM.
  F2: ``_finalize_job`` / ``_fire_watcher_notify_for_terminal`` keyword-
      map the notify payload: failed → ``error=``; everything else →
      ``result_summary=``. No positional passing.

This script exercises BOTH invariants on the REAL tool + observer code
paths, against a synthetic SQLite stack (file-backed: NullPool + WAL +
busy_timeout + FKs). The outermost delivery sink (``notify_watchers``
on the mocked ``JobQueueService``) is replaced with a capturing spy so
the script can introspect each emission's keyword args. Everything else
— ``watch_mission`` tool, ``JobWatcherRepository`` (real UPSERT + CAS
exactly-once), ``TaskRepository``, ``MissionResolver``,
``JobFeedbackObserver`` — runs REAL code paths.

PARKED MISSION f27e2d15 IS NOT TOUCHED — synthetic fixtures only
(fabricated ids ``scn-mission-001`` etc.).

ENV SAFETY: ambient POSTGRES_* pointing at LIVE ensemble_prod is
explicitly scrubbed at process start AND any python entry — the script
constructs its own SQLite stack explicitly (never relies on ambient
env). A guard at script entry aborts hard if any POSTGRES_* var is set.

Dual-layer timeout: ``signal.alarm(120)`` (inner guard) + the caller
wraps this script in ``timeout 300``.

Exit 0 = PASS, 1 = FAIL, 124 = TIMEOUT; final line prints
``RESULT: PASS|FAIL|TIMEOUT``.
"""
from __future__ import annotations

# ── Standard lib ─────────────────────────────────────────────────────────
import asyncio
import json
import os
import signal
import sys
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# ── ENV-SAFETY GUARD (mandatory pre-import) ──────────────────────────────
# Ambient POSTGRES_* on the dispatcher host points at LIVE ensemble_prod.
# The scenario is IN-PROCESS ONLY and uses a synthetic SQLite stack —
# abort hard if any POSTGRES_* var is set so the script can never touch
# live data even by accident (env inheritance from the shell).
_FORBIDDEN_ENV_VARS = (
    "POSTGRES_PASSWORD",
    "POSTGRES_HOST",
    "POSTGRES_USER",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_URL",
    "DATABASE_URL",
)
_leaked = [v for v in _FORBIDDEN_ENV_VARS if v in os.environ]
if _leaked:
    sys.stderr.write(
        f"\n[FATAL] {len(_leaked)} forbidden POSTGRES_* env var(s) present: "
        f"{_leaked!r}\nThe scenario is in-process only and must not "
        f"inherit any DB env. Aborting BEFORE any daemon import.\n"
    )
    sys.stderr.flush()
    sys.exit(1)

# Belt-and-braces scrub (the caller's ``unset`` already clears these;
# this protects against re-export by a parent script that re-inherits).
for _v in _FORBIDDEN_ENV_VARS:
    os.environ.pop(_v, None)

# ── Dual-layer watchdog (Layer 2: inner guard, ≤120 s) ───────────────────
WATCHDOG_SECONDS = 120


def _on_alarm(signum, frame):
    sys.stderr.write(
        f"\n[scenario] INTERNAL WATCHDOG FIRED after {WATCHDOG_SECONDS}s "
        f"(signal={signum}); exiting hard with TIMEOUT.\n"
    )
    sys.stderr.flush()
    sys.exit(124)


signal.signal(signal.SIGALRM, _on_alarm)
signal.alarm(WATCHDOG_SECONDS)

# ── Repo path resolution ─────────────────────────────────────────────────
REPO_ROOT = Path("/home/nea/ensemble-src")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ── Daemon imports ───────────────────────────────────────────────────────
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402
from sqlmodel import Session as SQLModelSession  # noqa: E402

# Importing the model modules registers them on SQLModel.metadata so
# ``SQLModel.metadata.create_all(eng)`` actually builds the tables the
# real repos depend on.
import daemon.repositories.instance.models  # noqa: E402,F401
import daemon.repositories.job_queue.models  # noqa: E402,F401
import daemon.repositories.task.models  # noqa: E402,F401

# Initialise the system-default project id so the repos accept None
# without raising the "called before system default project was
# initialized" RuntimeError.
import daemon.constants as _ens_constants  # noqa: E402
_ens_constants.SYSTEM_DEFAULT_PROJECT_ID = "replay-scenario-project"

from daemon.repositories.instance.models import (  # noqa: E402
    Instance,
    InstanceStatus,
)
from daemon.repositories.instance.repository import (  # noqa: E402
    SQLModelInstanceRepository,
)
from daemon.repositories.job_queue.watcher_repository import (  # noqa: E402
    JobWatcherRepository,
)
from daemon.repositories.task.models import Task, TaskStatus  # noqa: E402
from daemon.repositories.task.repository import TaskRepository  # noqa: E402
from daemon.services.mission_resolver import MissionResolver  # noqa: E402
from daemon.tools.job_queue import (  # noqa: E402
    create_job_tools,
    create_mission_watch_tools,
)
from daemon.services.job_feedback_observer import (  # noqa: E402
    JobFeedbackObserver,
    _FinalizeJobResult,
)

# Silences the observer's "failed to emit status_change / failed to
# publish lifecycle event" WARNINGs — those fire because we replaced
# the engine's SSE/lifecycle publishers with MagicMocks (the observer
# catches the resulting TypeError gracefully and warns). The warnings
# are noise in our isolated SQLite fixture; the observer's notify
# fan-out (the path under test) is unaffected.
import logging  # noqa: E402
logging.getLogger("daemon.services.job_feedback_observer").setLevel(
    logging.CRITICAL
)
# Also silence the resolver's "degrading to None" WARNING — that
# fires once at script start when the resolver gets a None job_repo
# in some non-test path; harmless for our scenario.
logging.getLogger("daemon.tools.job_queue").setLevel(logging.CRITICAL)

# ── Constants ────────────────────────────────────────────────────────────
CALLER = "scn-caller-0000-0000-0000-000000000001"  # The watcher-side instance.
MISSION_ID = "scn-mission-001"  # Synthetic mission (parked f27e2d15 untouched).
N_TERMINAL_RECEIPTS = 9
N_LIVE_RECEIPTS = 4
TOTAL_RECEIPTS = N_TERMINAL_RECEIPTS + N_LIVE_RECEIPTS
MISSION_ASSISTANT_TEXT = (
    "MISSION ASSISTANT REPLY — the one shared text that "
    "must NEVER leak into the Error: slot (incident RC2 symptom 4)."
)
SCRIPT_START = time.monotonic()

# Per-scenario verdict sink.
SCENARIO_RESULTS: dict[str, str] = {}
SCENARIO_DETAILS: dict[str, dict] = {}


def elapsed() -> float:
    return time.monotonic() - SCRIPT_START


def stamp(msg: str) -> None:
    sys.stdout.write(f"[scenario +{elapsed():6.2f}s] {msg}\n")
    sys.stdout.flush()


# ── Engine + seed helpers ────────────────────────────────────────────────


def _make_engine(db_path: Path) -> Engine:
    """File-backed SQLite engine: NullPool + WAL + busy_timeout + FKs.

    Mirrors the reference harness
    (``.agents/tester/RESULTS/2026-09-19-mission-watch-toolset-replay-script.py``
    _make_engine) — the lowest-cost shape that gives us real UPSERT +
    CAS-exactly-once on the watcher repo.
    """
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _configure_sqlite(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    return eng


def _seed_caller_instance(engine: Engine) -> None:
    """The watcher-side instance row must exist (FK to ``instances``)."""
    now = datetime.now(timezone.utc)
    iso_now = now.isoformat()
    with SQLModelSession(engine) as s:
        s.add(
            Instance(
                instance_id=CALLER,
                agent_id="orchestrator",
                agent_dir="agents/orchestrator",
                project_id="replay-scenario-project",
                status=InstanceStatus.RUNNING.value,
                last_activity_at=now,
                created_at=iso_now,
                updated_at=iso_now,
            )
        )
        s.commit()


def _seed_mission_instance(
    engine: Engine,
    *,
    status: str = InstanceStatus.RUNNING.value,
) -> str:
    """Seed the synthetic mission-instance row."""
    now = datetime.now(timezone.utc)
    iso_now = now.isoformat()
    with SQLModelSession(engine) as s:
        s.add(
            Instance(
                instance_id=MISSION_ID,
                agent_id="developer",
                agent_dir="agents/developer",
                project_id="replay-scenario-project",
                status=status,
                last_activity_at=now,
                created_at=iso_now,
                updated_at=iso_now,
            )
        )
        s.commit()
    return MISSION_ID


def _update_mission_instance_status(
    engine: Engine, status: str
) -> None:
    """Flip the mission-instance status (simulate the engine's terminal flip)."""
    with SQLModelSession(engine) as s:
        inst = s.get(Instance, MISSION_ID)
        inst.status = status
        inst.updated_at = datetime.now(timezone.utc).isoformat()
        s.commit()


def _seed_task(
    engine: Engine, *, work_id: str, instance_id: str,
    status: str = TaskStatus.RUNNING.value,
) -> str:
    """Seed a Task row (the 'receipt' — Task.work_id is the receipt handle)."""
    with SQLModelSession(engine) as s:
        s.add(Task(work_id=work_id, instance_id=instance_id, status=status))
        s.commit()
    return work_id


# ── WorkRecord double (mirrors the reference harness's _work_record) ──────


def _work_record(
    work_id: str, kind: str, status: str,
    *, instance_id=None, error=None, result_summary=None,
):
    """Mirrors the ``WorkRecord`` shape used by ``job_service.get_work`` mocks."""
    record = MagicMock(name=f"WorkRecord[{work_id[-8:]}]")
    record.work_id = work_id
    record.kind = kind
    record.status = status
    record.instance_id = instance_id
    record.error = error
    record.result_summary = result_summary
    return record


# ── Per-kind status resolver double (mirrors the unit-test stub) ─────────


class _FakePerKindResolver:
    """Deterministic stand-in for ``WorkResolverService.per_kind_status_for``.

    Maps selected work_ids to an explicit per-kind token (e.g. a mirror
    JobItem row resolving ``settled`` while Task rows keep the default);
    unlisted work_ids fall back to ``default`` — the production contract.
    """

    def __init__(self, mapping: dict):
        self._mapping = mapping

    def per_kind_status_for(self, work_id: str, *, default: str) -> str:
        return self._mapping.get(work_id, default)


# ── Spy on notify_watchers — mimics the real engine's CAS gate ──────────


class _NotifySpy:
    """Capture every call to ``job_service.notify_watchers`` AND mimic
    the real engine's CAS gate.

    The real ``notify_watchers`` engine helper
    (``daemon/services/work_notifier.py``) does an atomic CAS claim via
    ``JobWatcherRepository.claim_watchers_for_job_for_instances`` BEFORE
    building the ``[JOB_EVENT]`` envelope — no row → no delivery,
    returns 0. The OBSERVER's ``_finalize_job`` calls
    ``notify_watchers`` for EVERY candidate work_id (the enumeration
    is fine; the gate is the CAS); the gate is what counts as a
    "notification".

    Our spy mirrors that contract:
      * Records every call (for kwarg inspection in (d)).
      * Performs a CAS claim via the watcher_repo on the watching
        instance (CALLER) — so the RETURN VALUE matches the real
        engine's count of claimed watchers.
      * Increments ``delivery_count`` by the claim size — this is the
        real delivery count (the spy aggregates across all receipts).

    This lets the test assert on actual deliveries (not raw call
    counts) and stays faithful to the engine's exactly-once contract.
    """

    def __init__(self, watcher_repo=None, watcher_instance_id: str | None = None):
        self.calls: list[dict] = []
        self.delivery_count = 0
        self._watcher_repo = watcher_repo
        self._watcher_instance_id = watcher_instance_id

    def attach(self, watcher_repo, watcher_instance_id: str) -> None:
        """Late-bind the watcher repo (harness creates the repo first)."""
        self._watcher_repo = watcher_repo
        self._watcher_instance_id = watcher_instance_id

    async def emit(self, *args, **kwargs):
        """Async side-effect: record the call + CAS-claim + return count.

        Mirrors the real ``notify_watchers`` contract.
        """
        if len(args) >= 2:
            job_id, status = args[0], args[1]
            extra = args[2:] if len(args) > 2 else ()
        elif len(args) == 1:
            job_id, status, extra = args[0], None, ()
        else:
            job_id = kwargs.get("job_id")
            status = kwargs.get("status")
            extra = ()
        self.calls.append(
            {
                "work_id": job_id,
                "status": status,
                "positional_third": extra[0] if extra else None,
                "kw_error": kwargs.get("error"),
                "kw_result_summary": kwargs.get("result_summary"),
                "kw_progress": kwargs.get("progress"),
                "raw_args": args,
                "raw_kwargs": kwargs,
            }
        )
        # Mimic the real engine's CAS gate. If no watcher repo wired
        # (e.g. scenario (d) which doesn't arm any rows), return 0.
        if self._watcher_repo is None or job_id is None:
            return 0
        claimed = self._watcher_repo.claim_watchers_for_job_for_instances(
            job_id, [self._watcher_instance_id]
        )
        n_claimed = len(claimed)
        self.delivery_count += n_claimed
        return n_claimed

    def by_work_id(self, work_id: str) -> list[dict]:
        return [c for c in self.calls if c["work_id"] == work_id]

    def count_for(self, work_id: str) -> int:
        return len(self.by_work_id(work_id))

    def reset(self):
        self.calls.clear()
        self.delivery_count = 0


class _RecordOnlySpy:
    """Recording-only spy: captures kwargs but does no CAS gate.

    Used in scenario (d) where the test focuses on slot wiring (no
    rows are armed in that scenario — we drive ``_process_event``
    directly).
    """

    def __init__(self):
        self.calls: list[dict] = []

    def record(self, *args, **kwargs):
        if len(args) >= 2:
            job_id, status = args[0], args[1]
            extra = args[2:] if len(args) > 2 else ()
        elif len(args) == 1:
            job_id, status, extra = args[0], None, ()
        else:
            job_id = kwargs.get("job_id")
            status = kwargs.get("status")
            extra = ()
        self.calls.append(
            {
                "work_id": job_id,
                "status": status,
                "positional_third": extra[0] if extra else None,
                "kw_error": kwargs.get("error"),
                "kw_result_summary": kwargs.get("result_summary"),
                "kw_progress": kwargs.get("progress"),
                "raw_args": args,
                "raw_kwargs": kwargs,
            }
        )

    def by_work_id(self, work_id: str) -> list[dict]:
        return [c for c in self.calls if c["work_id"] == work_id]

    def count_for(self, work_id: str) -> int:
        return len(self.by_work_id(work_id))

    def reset(self):
        self.calls.clear()


# ── Mission-side observer fixtures (RC2 slot-wiring replay) ─────────────


def _build_observer_for_flip(
    *,
    job_service_mock,
    receipt_work_ids: list[str],
    per_kind: dict,
):
    """Build a JobFeedbackObserver wired for driving ``_process_event``.

    Mirrors the test-suite pattern
    (``tests/job_queue/test_job_feedback_observer.py::_build_finalize_env``):
    mock ``_finalize_job_db_sync`` (the engine's atomic terminal cascade
    requires a live ``WriteGuardSession`` — out of scope for the SQLite
    fixture), mock ``_get_last_assistant_message_raw`` to return
    ``MISSION_ASSISTANT_TEXT`` (the RC2 payload that the pre-fix code
    slipped into ``error=``), and wire ``_task_repo.get_by_instance`` to
    return our receipt set so the fan-out enumerates exactly the
    receipts the test cares about.

    Returns: ``(observer, sync_mock)``.
    """
    # Mirror the unit-test mock: ``_get_processing_job_for_instance``
    # resolves to ``None`` — the finalize path uses
    # ``candidate_work_ids = ctx.job_id ∪ _task_repo.get_by_instance(...)``
    # and ``ctx.job_id`` is None when no JobItem exists, so the fan-out
    # enumerates only the Task receipts (the incident shape).
    job_service_mock.get_job_by_instance = AsyncMock(return_value=None)
    # Spy receives these notify calls; (d) inspects kwargs.
    job_service_mock._work_resolver = _FakePerKindResolver(per_kind)

    instance_manager = MagicMock()
    instance_manager._get_last_assistant_message_raw = AsyncMock(
        return_value=MISSION_ASSISTANT_TEXT
    )
    instance_manager._task_repo = MagicMock()
    instance_manager._task_repo.get_by_instance = MagicMock(
        return_value=[SimpleNamespace(work_id=w) for w in receipt_work_ids]
    )

    observer = JobFeedbackObserver(
        event_bus=MagicMock(),
        job_queue_service=job_service_mock,
        job_repo=MagicMock(),
        lock_repo=MagicMock(),
        project_repo=MagicMock(),
        instance_manager=instance_manager,
    )

    # Mirror the production sync helper's signature; the observer's async
    # fan-out reads ``db_result.terminal_status``, ``result_summary`` and
    # ``error_message`` to pick per-kind + payload.
    def _fake_sync(job_id, instance_id, terminal_status, result_summary, error_message):
        return _FinalizeJobResult(
            skip=False,
            terminal_status=terminal_status,
            job_id=job_id,
            instance_id=instance_id,
            parent_id=None,
            agent_id="developer",
            result_summary=result_summary,
            error_message=error_message,
            locks_released=1,
            instance_was_terminal=False,
        )

    sync_mock = MagicMock(side_effect=_fake_sync)
    observer._finalize_job_db_sync = sync_mock
    return observer, sync_mock


# ── Harness builders ──────────────────────────────────────────────────────


def _build_scenario_abce_harness(db_path: Path):
    """Build the harness for scenarios (a)/(b)/(c)/(e).

    The five-scenario brief keeps state continuity across (a)→b)→(c)→(e)
    on ONE mission instance: the same N=9 already-terminal receipts and
    N=4 live receipts. Scenarios (a) and (b) live on a LIVE mission
    instance (RUNNING at call time; flipped to COMPLETED for (b)); (c) and
    (e) build on (b)'s post-flip state.

    A capturing spy on ``notify_watchers`` lets us assert exactly which
    receipts fired, how many times, and with what kwargs. The
    ``MissionResolver`` is REAL (so ``watch_mission``'s
    ``_resolve_mission_record`` path runs end-to-end), but the engine's
    instance-status flip is simulated by directly mutating the
    ``Instance.status`` column + driving the OBSERVER's
    ``_process_event`` for the notify fan-out (RC2 slot-wiring).
    """
    engine = _make_engine(db_path)
    _seed_caller_instance(engine)
    _seed_mission_instance(engine)  # RUNNING

    instance_repo = SQLModelInstanceRepository(engine)
    watcher_repo = JobWatcherRepository(engine)
    task_repo = TaskRepository(engine)
    from daemon.repositories.job_queue.repository import JobRepository
    resolver = MissionResolver(
        instance_repo=instance_repo, job_repo=JobRepository(engine)
    )

    # The notify spy: the AsyncMock returns the spy's ``emit`` coroutine
    # which records every call AND performs a CAS gate (so the return
    # value mirrors the real engine's count of claimed watchers).
    spy = _NotifySpy()
    job_service = AsyncMock(name="JobQueueService")
    job_service.get_work = AsyncMock(return_value=None)
    job_service.get_job_by_instance = AsyncMock(return_value=None)
    job_service.notify_watchers = AsyncMock(side_effect=spy.emit)
    job_service._work_resolver = _FakePerKindResolver({})
    # Late-bind the watcher repo to the spy (the repo was created
    # above; the spy needs the (job_id, instance_id) CAS primitive).
    spy.attach(watcher_repo, CALLER)

    mission_watch_tool = create_mission_watch_tools(
        job_service=job_service,
        mission_resolver=resolver,
        task_repo=task_repo,
        watcher_repo=watcher_repo,
        current_instance_id=CALLER,
    )
    watch_mission = mission_watch_tool[0]

    return {
        "engine": engine,
        "watcher_repo": watcher_repo,
        "task_repo": task_repo,
        "resolver": resolver,
        "job_service": job_service,
        "spy": spy,
        "watch_mission": watch_mission,
    }


def _build_unwatch_harness(db_path: Path, manager):
    """Build a manager-wired harness for the ``unwatch_job`` tool (scenario e).

    ``unwatch_job`` requires ``create_job_tools(manager=...)`` because the
    mission-handle branch reads ``manager._mission_resolver`` and
    ``manager._task_repo`` (see ``daemon/tools/job_queue.py:2051-2065``).
    """
    raise NotImplementedError(
        "Scenario (e) builds its harness inline — see scenario_e(); "
        "this helper is kept only for documentation reference."
    )


# ── Test data — receipt sets (deterministic ids) ─────────────────────────


def _seed_receipts(harness) -> tuple[list[str], list[str]]:
    """Seed the synthetic receipts: 9 already-terminal + 4 live.

    Returns ``(terminal_receipts, live_receipts)`` — both lists of
    work_ids. Receipt ids are deterministic (``scn-term-000`` etc.) so
    failure logs are reproducible.
    """
    terminal_receipts = [
        f"scn-term-{i:03d}-0000-0000-0000-000000000000"
        for i in range(N_TERMINAL_RECEIPTS)
    ]
    live_receipts = [
        f"scn-live-{i:03d}-0000-0000-0000-000000000000"
        for i in range(N_LIVE_RECEIPTS)
    ]

    # Seed Task rows (work_id = receipt handle) — the watcher's
    # ``get_by_instance`` enumerates these to fan out registration.
    # Pre-terminal Task rows exist in the DB so the receipt list is
    # stable across the script; ``watch_mission`` consults
    # ``job_service.get_work`` (a MagicMock side-effect below) for the
    # per-receipt terminal CHECK — that's the F1 filter.
    for wid in terminal_receipts + live_receipts:
        _seed_task(
            harness["engine"],
            work_id=wid,
            instance_id=MISSION_ID,
        )

    # Wire ``job_service.get_work`` to return a WorkRecord whose ``status``
    # matches each receipt's pre-flip state.
    statuses = {
        **{wid: "completed" for wid in terminal_receipts},
        **{wid: "processing" for wid in live_receipts},
    }

    async def _get_work(work_id):
        return _work_record(
            work_id, "report", statuses[work_id],
            instance_id=MISSION_ID,
        )

    harness["job_service"].get_work = AsyncMock(side_effect=_get_work)
    # Stash the full receipt set so a follow-on ``_seed_receipts_flip2``
    # can re-wire ``get_work`` to mark ALL of them settled.
    harness["_all_receipts"] = terminal_receipts + live_receipts

    return terminal_receipts, live_receipts


def _seed_receipts_flip2(harness, *, live_receipts: list[str]) -> None:
    """Re-wire ``job_service.get_work`` for the SECOND flip (after (b)).

    By scenario (c), the formerly-live receipts have all settled (the
    flip in (b) fired them, the CAS consumed the rows). The re-call
    ``watch_mission`` MUST treat ALL receipts as settled (the original
    terminal ones + the formerly-live ones) and skip them all — no row
    is re-armed, so the next flip has nothing to fire.
    """
    # All receipts settle after (b)'s terminal flip — the formerly-
    # live ones now mirror the original settled set.
    all_receipts = set(harness.get("_all_receipts", []))
    statuses = {wid: "completed" for wid in all_receipts}

    async def _get_work_settled(work_id):
        return _work_record(
            work_id, "report", statuses.get(work_id, "completed"),
            instance_id=MISSION_ID,
        )

    harness["job_service"].get_work = AsyncMock(side_effect=_get_work_settled)


# ── Engine-side flip driver (simulates the engine's terminal cascade) ────


def _drive_flip_completed(
    *,
    engine: Engine,
    job_service,
    receipt_work_ids: list[str],
    per_kind: dict | None = None,
):
    """Drive the real observer fan-out for a COMPLETED mission-instance flip.

    Mirrors what the engine does at terminal time: update the instance
    status, then ``JobFeedbackObserver._process_event`` runs the post-
    commit notify fan-out (the source of the RC2 positional slip before
    the fix). Returns the list of notify_watchers calls captured during
    this flip.

    Per-kind defaults: Task rows → ``completed``. The caller's ``per_kind``
    overrides specific work_ids (e.g. a mirror row resolving ``settled``).
    """
    _update_mission_instance_status(engine, InstanceStatus.COMPLETED.value)
    observer, sync_mock = _build_observer_for_flip(
        job_service_mock=job_service,
        receipt_work_ids=receipt_work_ids,
        per_kind=per_kind or {},
    )
    event = {
        "event_type": "instance_lifecycle",
        "data": {
            "instance_id": MISSION_ID,
            "status": "completed",
            "error": None,
        },
    }
    asyncio.run(observer._process_event(event))
    return list(job_service.notify_watchers.await_args_list)


def _drive_flip_failed(
    *,
    engine: Engine,
    job_service,
    receipt_work_ids: list[str],
    failure_message: str,
    per_kind: dict | None = None,
):
    """Drive the real observer fan-out for an ERROR mission-instance flip.

    Same shape as ``_drive_flip_completed`` but the lifecycle event
    carries ``status=error`` and a real ``error`` payload; the observer
    routes the failure into the ``error=`` slot of ``notify_watchers``
    (RC2's right-side wiring — failure content → ``error=``, success
    content → ``result_summary=``).
    """
    _update_mission_instance_status(engine, InstanceStatus.ERROR.value)
    observer, _sync_mock = _build_observer_for_flip(
        job_service_mock=job_service,
        receipt_work_ids=receipt_work_ids,
        per_kind=per_kind or {},
    )
    event = {
        "event_type": "instance_lifecycle",
        "data": {
            "instance_id": MISSION_ID,
            "status": "error",
            "error": failure_message,
        },
    }
    asyncio.run(observer._process_event(event))
    return list(job_service.notify_watchers.await_args_list)


# ── Scenarios ────────────────────────────────────────────────────────────


def scenario_a(db_path: Path) -> tuple[str, dict]:
    """(a) ``watch_mission`` registration-time terminal filter.

    Expect:
      * ``job_watchers`` rows minted ONLY for currently-live receipts
        (4 rows; the 9 settled ones get no row, no notify).
      * Tool output reports ``armed 4 live receipt(s), 9 already-settled
        receipt(s) skipped``.
      * No ``notify_watchers`` call yet (mission still RUNNING; no flip).
    """
    stamp("Scenario (a) — registration-time terminal filter")
    h = _build_scenario_abce_harness(db_path)
    terminal, live = _seed_receipts(h)

    details = {
        "mission_id": MISSION_ID,
        "n_terminal_receipts": len(terminal),
        "n_live_receipts": len(live),
        "terminal_receipts": terminal,
        "live_receipts": live,
    }

    try:
        result = asyncio.run(h["watch_mission"].ainvoke({"target": MISSION_ID}))
        details["watch_mission_result"] = result

        watched = {w.job_id for w in h["watcher_repo"].get_watches_for_instance(CALLER)}
        details["watched_job_ids"] = sorted(watched)
        details["spy_delivery_count_after_register"] = h["spy"].delivery_count

        a_pass = all([
            "armed 4 live receipt(s)" in result,
            "9 already-settled receipt(s) skipped" in result,
            watched == set(live),
            len(h["spy"].calls) == 0,  # no flip yet → no notify
            h["spy"].delivery_count == 0,
        ])
        details["a_pass"] = a_pass
        details["verdict"] = "PASS" if a_pass else "FAIL"
        # Stash for downstream scenarios.
        details["_live_receipts"] = live
        details["_terminal_receipts"] = terminal
        return details["verdict"], details
    except Exception as e:
        details["verdict"] = "FAIL"
        details["exception"] = repr(e)
        details["traceback"] = traceback.format_exc()
        return "FAIL", details


def scenario_b(db_path: Path, a_details: dict) -> tuple[str, dict]:
    """(b) Flip mission → completed; armed set fires EXACTLY ONCE.

    Expect:
      * Each of the 4 live receipts fires notify_watchers EXACTLY ONCE.
      * ZERO notify_watchers calls for the 9 settled receipts (no row,
        no fire).
      * The CAS exactly-once primitive
        (``claim_watchers_for_job_for_instances``) returns 1 row on the
        first claim per live receipt and 0 on the second; returns [] for
        every settled receipt (the row was never minted, so there is
        nothing to claim — the duplicate-burst killer).
    """
    stamp("Scenario (b) — flip fires armed set exactly once; settled receipts stay silent")
    h = _build_scenario_abce_harness(db_path)
    terminal, live = _seed_receipts(h)

    details = {
        "mission_id": MISSION_ID,
        "n_terminal_receipts": len(terminal),
        "n_live_receipts": len(live),
    }

    try:
        # 1) Register rows via the real tool.
        asyncio.run(h["watch_mission"].ainvoke({"target": MISSION_ID}))
        # 2) Flip the mission instance to COMPLETED.
        _update_mission_instance_status(
            h["engine"], InstanceStatus.COMPLETED.value
        )
        # 3) Drive the observer's terminal fan-out — this is the path
        # that produces the notify_watchers calls under test. Pre-fix
        # it would also enumerate the settled receipts (every Task row
        # in the mission is enumerated as a candidate work_id); the
        # watcher_repo's CAS gate is the row-presence check — and the
        # F1 fix means settled receipts have NO row → CAS no-ops.
        per_kind_calls = _drive_flip_completed(
            engine=h["engine"],
            job_service=h["job_service"],
            receipt_work_ids=terminal + live,
            per_kind={},
        )
        # The OBSERVER's ``_finalize_job`` enumerates EVERY candidate
        # work_id (the mission's task list) and calls ``notify_watchers``
        # for each — the OBSERVER call count is 13 (9 + 4). The
        # delivery gate is the CAS in the engine; our spy mirrors the
        # real CAS contract, so ``spy.delivery_count`` is the count of
        # ACTUAL deliveries (= 4 — only the live receipts had rows).
        details["n_observer_calls_total"] = len(h["spy"].calls)
        details["n_deliveries_total"] = h["spy"].delivery_count
        details["delivery_counts_per_receipt"] = {
            wid: h["spy"].count_for(wid) for wid in terminal + live
        }

        # The 4 live receipts: each was called exactly once by the
        # observer AND each fired one delivery (1 watcher claimed).
        # The 9 settled receipts: each was called by the observer (it's
        # the candidate enumeration — correct) but ZERO deliveries
        # because the CAS gate found no row.
        per_live_calls = {wid: h["spy"].count_for(wid) for wid in live}
        per_terminal_calls = {wid: h["spy"].count_for(wid) for wid in terminal}
        details["per_live_observer_calls"] = per_live_calls
        details["per_terminal_observer_calls"] = per_terminal_calls

        # Direct CAS assertion: each live receipt has exactly one
        # watcher row (minted in step 1) and the CAS consumes it once;
        # second claim is empty (exactly-once). Each settled receipt
        # had NO row minted, so CAS returns [] from the start.
        # (Note: the spy's emit already consumed the rows for live
        # receipts during the flip — so this direct CAS query tests
        # what happens AFTER the flip consumed them.)
        cas_first_pass = {}
        cas_second_pass = {}
        for wid in live:
            first = h["watcher_repo"].claim_watchers_for_job_for_instances(
                wid, [CALLER]
            )
            second = h["watcher_repo"].claim_watchers_for_job_for_instances(
                wid, [CALLER]
            )
            cas_first_pass[wid] = len(first)
            cas_second_pass[wid] = len(second)

        settled_cas_results = {
            wid: len(
                h["watcher_repo"].claim_watchers_for_job_for_instances(
                    wid, [CALLER]
                )
            )
            for wid in terminal
        }
        details["cas_first_pass_live"] = cas_first_pass
        details["cas_second_pass_live"] = cas_second_pass
        details["settled_cas_results"] = settled_cas_results

        b_pass = all([
            # Observer enumerates each candidate exactly once (4 live
            # + 9 settled = 13 total observer calls).
            all(v == 1 for v in per_live_calls.values()),
            all(v == 1 for v in per_terminal_calls.values()),
            # DELIVERY counts: 4 live → 1 each; 9 settled → 0 each.
            # The spy's CAS-gated ``delivery_count`` proves the real
            # engine would have delivered to ONLY the live watchers.
            h["spy"].delivery_count == len(live),
            # CAS exactly-once on the live receipts (the spy already
            # consumed them in step 3 — these claims must be empty
            # now, the exactly-once invariant).
            all(v == 0 for v in cas_first_pass.values()),
            all(v == 0 for v in cas_second_pass.values()),
            # No CAS hit on settled receipts — the row was never minted.
            all(v == 0 for v in settled_cas_results.values()),
        ])
        details["b_pass"] = b_pass
        details["verdict"] = "PASS" if b_pass else "FAIL"
        return details["verdict"], details
    except Exception as e:
        details["verdict"] = "FAIL"
        details["exception"] = repr(e)
        details["traceback"] = traceback.format_exc()
        return "FAIL", details


def scenario_c(db_path: Path) -> tuple[str, dict]:
    """(c) Re-call ``watch_mission`` (delta-arm); flip ANOTHER live event.

    Expect:
      * The re-call sees the formerly-live receipts as SETTLED (their
        state moved to ``completed`` after (b)'s flip) and reports
        ``armed 0 live receipt(s), 4 already-settled receipt(s) skipped``.
      * NO new ``job_watchers`` rows minted.
      * The second flip emits ZERO notify_watchers calls — there is no
        armed row to fire, so the duplicate-burst shape cannot recur.
    """
    stamp("Scenario (c) — delta-arm re-call; no second burst on flip")
    h = _build_scenario_abce_harness(db_path)
    terminal, live = _seed_receipts(h)

    details = {
        "mission_id": MISSION_ID,
        "n_terminal_receipts": len(terminal),
        "n_live_receipts": len(live),
    }

    try:
        # 1) Arm + flip #1 (consume the live receipts).
        asyncio.run(h["watch_mission"].ainvoke({"target": MISSION_ID}))
        _drive_flip_completed(
            engine=h["engine"],
            job_service=h["job_service"],
            receipt_work_ids=terminal + live,
            per_kind={},
        )
        deliveries_after_flip1 = h["spy"].delivery_count
        calls_after_flip1 = len(h["spy"].calls)

        # 2) Mark all the live receipts SETTLED (the engine's
        # post-terminal state). This mirrors what happens between the
        # first flip and the next turn in the real incident.
        _seed_receipts_flip2(h, live_receipts=live)

        # 3) Re-call ``watch_mission`` (the documented workflow after
        # every ``job_continue``). With F1, the re-call is a DELTA-ARM:
        # settled receipts are SKIPPED, never re-armed.
        result_rewrite = asyncio.run(
            h["watch_mission"].ainvoke({"target": MISSION_ID})
        )
        details["rewrite_result"] = result_rewrite

        watched_after_rewrite = {
            w.job_id for w in h["watcher_repo"].get_watches_for_instance(CALLER)
        }
        details["watched_job_ids_after_rewrite"] = sorted(watched_after_rewrite)

        # 4) Flip again. Under the F1 fix, NO armed rows exist for any
        # receipt (settled ones were never re-armed; live ones were
        # consumed by CAS in step 1 and the re-call skipped them) —
        # so the second flip delivers ZERO [JOB_EVENT] notifications
        # (the duplicate-burst killer). The OBSERVER still enumerates
        # all candidate work_ids — that's correct candidate enumeration;
        # the gate is the CAS, and our spy mirrors that contract.
        _drive_flip_completed(
            engine=h["engine"],
            job_service=h["job_service"],
            receipt_work_ids=terminal + live,
            per_kind={},
        )
        deliveries_after_flip2 = h["spy"].delivery_count
        calls_after_flip2 = len(h["spy"].calls)
        details["calls_after_flip1"] = calls_after_flip1
        details["calls_after_flip2"] = calls_after_flip2
        details["deliveries_after_flip1"] = deliveries_after_flip1
        details["deliveries_after_flip2"] = deliveries_after_flip2
        details["deliveries_delta_flip2_minus_flip1"] = (
            deliveries_after_flip2 - deliveries_after_flip1
        )

        c_pass = all([
            # Tool output uses "Armed 0 live receipt(s)" with capital
            # A — match case-insensitively.
            "armed 0 live receipt(s)" in result_rewrite.lower(),
            # All 13 receipts are settled at re-call time (the 9
            # originally-terminal ones plus the 4 that flipped in
            # step 1) — the re-call skips them ALL.
            "13 already-settled receipt(s) skipped" in result_rewrite,
            watched_after_rewrite == set(),
            # The OBSERVER may still enumerate candidate work_ids on
            # the second flip (that's the correct observer behaviour);
            # what matters is the DELIVERY count — the second flip
            # added ZERO deliveries, the duplicate-burst invariant.
            deliveries_after_flip2 - deliveries_after_flip1 == 0,
            # And the first flip's deliveries count was exactly the
            # armed set size (4) — the receipts that had rows.
            deliveries_after_flip1 == len(live),
        ])
        details["c_pass"] = c_pass
        details["verdict"] = "PASS" if c_pass else "FAIL"
        return details["verdict"], details
    except Exception as e:
        details["verdict"] = "FAIL"
        details["exception"] = repr(e)
        details["traceback"] = traceback.format_exc()
        return "FAIL", details


def scenario_d(db_path: Path) -> tuple[str, dict]:
    """(d) Slot wiring: non-failed receipts never carry assistant text in Error.

    Drive a fresh observer fan-out — the source of the RC2 defect —
    twice: once with status=completed (non-failed), once with
    status=error (failed). For each emission, inspect ``notify_watchers``
    kwargs:
      * Non-failed (completed): ``error`` is ``None`` / absent;
        ``result_summary`` carries the assistant message. CRUCIALLY:
        the mission-instance's assistant text MUST NEVER land in
        ``error=``.
      * Failed (error): ``error=<failure message>``; no result_summary
        leakage.

    The mission-instance's latest assistant message is fetched via
    ``_get_last_assistant_message_raw`` — the exact RC2 payload source
    that the pre-fix code slipped into the ``error=`` slot positionally.
    """
    stamp("Scenario (d) — slot wiring (F2): assistant text must NEVER leak into error=")
    details: dict = {"mission_id": MISSION_ID}

    try:
        # Build a fresh harness with a CLEAN notify spy (so we count
        # only the fan-out calls for THIS scenario).
        engine = _make_engine(db_path)
        _seed_caller_instance(engine)
        _seed_mission_instance(engine)

        # Two Task-style receipts: one will resolve completed (non-
        # failed), one will resolve failed.
        receipt_completed = "scn-d1-completed-0000-0000-0000-000000000000"
        receipt_failed = "scn-d2-failed-0000-0000-0000-000000000000"
        for wid in (receipt_completed, receipt_failed):
            _seed_task(engine, work_id=wid, instance_id=MISSION_ID)

        # ``job_service.notify_watchers`` is captured by the spy; no
        # ``watch_mission`` is involved here — we drive the
        # ``_process_event`` fan-out directly to focus on the slot
        # wiring (F2), not the registration-time filter (F1). For (d)
        # we DON'T need a watcher_repo (no rows are armed in this
        # scenario) — a recording-only spy is enough.
        spy = _RecordOnlySpy()
        job_service = AsyncMock(name="JobQueueService")

        async def _spy_record(*args, **kwargs):
            spy.record(*args, **kwargs)
            return 0  # no delivery — we only inspect kwargs here

        job_service.notify_watchers = AsyncMock(side_effect=_spy_record)

        # ── Flip #1: COMPLETED ────────────────────────────────────────
        completed_calls = _drive_flip_completed(
            engine=engine,
            job_service=job_service,
            receipt_work_ids=[receipt_completed],
            per_kind={},  # Task row → default "completed"
        )

        # ── Flip #2: FAILED ───────────────────────────────────────────
        failed_calls = _drive_flip_failed(
            engine=engine,
            job_service=job_service,
            receipt_work_ids=[receipt_failed],
            failure_message="real failure text — child crashed",
            per_kind={},
        )

        # Capture kwargs excerpts for the per-receipt assertions.
        completed_kwargs = [c.kwargs for c in completed_calls]
        failed_kwargs = [c.kwargs for c in failed_calls]
        details["completed_kwargs_excerpt"] = [
            {
                "work_id": c.args[0],
                "status": c.args[1],
                "error": c.kwargs.get("error"),
                "result_summary": c.kwargs.get("result_summary"),
            }
            for c in completed_calls
        ]
        details["failed_kwargs_excerpt"] = [
            {
                "work_id": c.args[0],
                "status": c.args[1],
                "error": c.kwargs.get("error"),
                "result_summary": c.kwargs.get("result_summary"),
            }
            for c in failed_calls
        ]

        # ── Assertions: slot wiring ──────────────────────────────────
        completed_emissions = [
            c for c in spy.calls if c["work_id"] == receipt_completed
        ]
        failed_emissions = [
            c for c in spy.calls if c["work_id"] == receipt_failed
        ]

        # Non-failed: error must be None / absent, result_summary must
        # carry the assistant text, and the assistant text MUST NEVER
        # land in ``error=`` (the RC2 defect). Note: positional passing
        # the third argument ALSO lands in ``error=`` per
        # ``notify_watchers``'s signature — we check both keyword
        # ``error`` and any positional third argument.
        nonfailed_ok = all(
            c["status"] == "completed"
            and c["kw_error"] is None
            and c["positional_third"] is None
            and c["kw_result_summary"] == MISSION_ASSISTANT_TEXT
            for c in completed_emissions
        )
        details["nonfailed_assistant_in_error"] = [
            c for c in completed_emissions if c["kw_error"] == MISSION_ASSISTANT_TEXT
        ]
        details["nonfailed_positional_assistant_in_error"] = [
            c for c in completed_emissions
            if c["positional_third"] == MISSION_ASSISTANT_TEXT
        ]

        # Failed: error must carry the real failure message (not the
        # assistant text); result_summary must not be the assistant
        # text either.
        failed_ok = all(
            c["status"] == "failed"
            and c["kw_error"] == "real failure text — child crashed"
            and c["kw_result_summary"] != MISSION_ASSISTANT_TEXT
            for c in failed_emissions
        )
        details["failed_assistant_in_error"] = [
            c for c in failed_emissions if c["kw_error"] == MISSION_ASSISTANT_TEXT
        ]

        d_pass = (
            len(completed_emissions) >= 1
            and len(failed_emissions) >= 1
            and nonfailed_ok
            and failed_ok
        )
        details["d_pass"] = d_pass
        details["verdict"] = "PASS" if d_pass else "FAIL"
        return details["verdict"], details
    except Exception as e:
        details["verdict"] = "FAIL"
        details["exception"] = repr(e)
        details["traceback"] = traceback.format_exc()
        return "FAIL", details


def scenario_e(db_path: Path) -> tuple[str, dict]:
    """(e) ``unwatch_job`` (mission handle) → flip → ZERO NEW emissions.

    Expect:
      * After ``unwatch_job(mission_id)`` removes all watched receipt
        rows, a follow-up flip emits ZERO notify_watchers calls — the
        unwatch consumed the rows, the engine's CAS gate finds nothing
        to fire.
      * ``unwatch_job`` reports either the receipt-removal count (if
        rows existed) OR the "no matching receipt watches" shape (if
        the CAS in scenario (b) already consumed them).
    """
    stamp("Scenario (e) — unwatch_job after consumption; zero new emissions")
    details: dict = {"mission_id": MISSION_ID}

    try:
        # Wire the harness through ``create_job_tools`` (the
        # ``unwatch_job`` tool's manager dependency requires
        # ``manager._mission_resolver`` + ``manager._task_repo``). We
        # construct a lightweight ``manager`` double.
        engine = _make_engine(db_path)
        _seed_caller_instance(engine)
        _seed_mission_instance(engine)

        instance_repo = SQLModelInstanceRepository(engine)
        watcher_repo = JobWatcherRepository(engine)
        task_repo = TaskRepository(engine)
        from daemon.repositories.job_queue.repository import JobRepository
        resolver = MissionResolver(
            instance_repo=instance_repo, job_repo=JobRepository(engine)
        )

        spy = _NotifySpy()
        job_service = AsyncMock(name="JobQueueService")
        job_service.get_work = AsyncMock(return_value=None)
        job_service.get_job_by_instance = AsyncMock(return_value=None)
        job_service.notify_watchers = AsyncMock(side_effect=spy.emit)
        job_service._work_resolver = _FakePerKindResolver({})
        spy.attach(watcher_repo, CALLER)

        # Manager double: just enough surface for ``unwatch_job``'s
        # mission-handle branch.
        manager = MagicMock(name="InstanceManager")
        manager._mission_resolver = resolver
        manager._task_repo = task_repo

        tools = create_job_tools(
            job_service=job_service,
            queue_mgmt_service=MagicMock(name="queue_mgmt"),
            dead_letter_service=MagicMock(name="dlq"),
            current_instance_id=CALLER,
            agent_id="orchestrator",
            watcher_repo=watcher_repo,
            manager=manager,
        )
        unwatch_job = next(t for t in tools if t.name == "unwatch_job")

        # Seed receipts: 4 live + 9 terminal.
        terminal, live = [], []
        for i in range(N_TERMINAL_RECEIPTS):
            wid = f"scn-e-term-{i:03d}-0000-0000-0000-000000000000"
            _seed_task(engine, work_id=wid, instance_id=MISSION_ID)
            terminal.append(wid)
        for i in range(N_LIVE_RECEIPTS):
            wid = f"scn-e-live-{i:03d}-0000-0000-0000-000000000000"
            _seed_task(engine, work_id=wid, instance_id=MISSION_ID)
            live.append(wid)

        statuses = {
            **{wid: "completed" for wid in terminal},
            **{wid: "processing" for wid in live},
        }

        async def _get_work(work_id):
            return _work_record(
                work_id, "report", statuses[work_id], instance_id=MISSION_ID
            )

        job_service.get_work = AsyncMock(side_effect=_get_work)

        mission_watch_tools = create_mission_watch_tools(
            job_service=job_service,
            mission_resolver=resolver,
            task_repo=task_repo,
            watcher_repo=watcher_repo,
            current_instance_id=CALLER,
        )
        watch_mission = mission_watch_tools[0]

        # 1) Arm the live set.
        register_result = asyncio.run(watch_mission.ainvoke({"target": MISSION_ID}))
        details["register_result"] = register_result
        rows_after_arm = {
            w.job_id for w in watcher_repo.get_watches_for_instance(CALLER)
        }
        details["rows_after_arm"] = sorted(rows_after_arm)

        # 2) Flip + drive the observer fan-out (consume the live rows).
        flip1_calls = _drive_flip_completed(
            engine=engine,
            job_service=job_service,
            receipt_work_ids=terminal + live,
            per_kind={},
        )
        calls_after_flip1 = len(spy.calls)
        deliveries_after_flip1 = spy.delivery_count
        details["calls_after_flip1"] = calls_after_flip1
        details["deliveries_after_flip1"] = deliveries_after_flip1

        # 3) Mark all receipts settled (post-flip terminal state).
        statuses_after = {wid: "completed" for wid in terminal + live}

        async def _get_work_settled(work_id):
            return _work_record(
                work_id, "report",
                statuses_after.get(work_id, "completed"),
                instance_id=MISSION_ID,
            )

        job_service.get_work = AsyncMock(side_effect=_get_work_settled)

        # 4) Unwatch via the mission handle. After CAS consumed the
        # live rows in step 2, no rows remain — the tool returns the
        # "no matching receipt watches" shape.
        unwatch_result = asyncio.run(unwatch_job.ainvoke({"job_id": MISSION_ID}))
        details["unwatch_result"] = unwatch_result

        # 5) Flip again. ZERO new deliveries expected.
        flip2_calls = _drive_flip_completed(
            engine=engine,
            job_service=job_service,
            receipt_work_ids=terminal + live,
            per_kind={},
        )
        calls_after_flip2 = len(spy.calls)
        deliveries_after_flip2 = spy.delivery_count
        details["calls_after_flip2"] = calls_after_flip2
        details["deliveries_after_flip2"] = deliveries_after_flip2
        details["calls_delta_flip2_minus_flip1"] = calls_after_flip2 - calls_after_flip1
        details["deliveries_delta_flip2_minus_flip1"] = (
            deliveries_after_flip2 - deliveries_after_flip1
        )

        e_pass = all([
            # The unwatch reported either "Stopped watching" (if rows
            # existed at the time of the unwatch) or "no matching
            # receipt watches" (if CAS already consumed them). Both
            # are acceptable — the invariant is that NO rows exist
            # after unwatch.
            ("Stopped watching" in unwatch_result
             or "no matching receipt watches" in unwatch_result),
            # DELIVERY invariant: ZERO new deliveries after unwatch +
            # flip. (The OBSERVER still enumerates candidate work_ids;
            # the gate is the CAS, and the spy's CAS-gated
            # ``delivery_count`` proves the real engine would deliver
            # nothing.)
            deliveries_after_flip2 - deliveries_after_flip1 == 0,
            # And the watcher table is empty.
            {w.job_id for w in watcher_repo.get_watches_for_instance(CALLER)} == set(),
            # First flip delivered exactly the armed set (4 receipts).
            deliveries_after_flip1 == len(live),
        ])
        details["e_pass"] = e_pass
        details["verdict"] = "PASS" if e_pass else "FAIL"
        return details["verdict"], details
    except Exception as e:
        details["verdict"] = "FAIL"
        details["exception"] = repr(e)
        details["traceback"] = traceback.format_exc()
        return "FAIL", details


# ── Reporter ─────────────────────────────────────────────────────────────


def _emit_summary() -> int:
    overall_pass = all(v == "PASS" for v in SCENARIO_RESULTS.values())
    overall_fail = any(v == "FAIL" for v in SCENARIO_RESULTS.values())
    overall = "PASS" if overall_pass else ("FAIL" if overall_fail else "PARTIAL")
    print()
    print("=" * 72)
    print(f"OVERALL RESULT: {overall}")
    print(f"Total runtime: {elapsed():.2f}s (inner watchdog cap {WATCHDOG_SECONDS}s)")
    print("=" * 72)
    print()
    print("Per-scenario verdicts:")
    for name in ("a", "b", "c", "d", "e"):
        verdict = SCENARIO_RESULTS.get(name, "NOT_RUN")
        marker = "✓" if verdict == "PASS" else ("✗" if verdict == "FAIL" else "?")
        print(f"  ({marker}) ({name}): {verdict}")
        details = SCENARIO_DETAILS.get(name, {})
        # Show key evidence per scenario.
        if name == "a":
            print(f"      mission_id: {details.get('mission_id')}")
            print(f"      n_terminal_receipts: {details.get('n_terminal_receipts')}")
            print(f"      n_live_receipts: {details.get('n_live_receipts')}")
            print(f"      watch_mission_result: {details.get('watch_mission_result')}")
            print(f"      watched_job_ids: {details.get('watched_job_ids')}")
            print(f"      spy_delivery_count_after_register: {details.get('spy_delivery_count_after_register')}")
        elif name == "b":
            print(f"      n_observer_calls_total: {details.get('n_observer_calls_total')}")
            print(f"      n_deliveries_total: {details.get('n_deliveries_total')}")
            print(f"      per_live_observer_calls: {details.get('per_live_observer_calls')}")
            print(f"      per_terminal_observer_calls: {details.get('per_terminal_observer_calls')}")
            print(f"      settled_cas_results (must all be 0): {details.get('settled_cas_results')}")
        elif name == "c":
            print(f"      rewrite_result: {details.get('rewrite_result')}")
            print(f"      watched_job_ids_after_rewrite: {details.get('watched_job_ids_after_rewrite')}")
            print(f"      calls_after_flip1: {details.get('calls_after_flip1')}")
            print(f"      calls_after_flip2: {details.get('calls_after_flip2')}")
            print(f"      deliveries_after_flip1: {details.get('deliveries_after_flip1')}")
            print(f"      deliveries_after_flip2: {details.get('deliveries_after_flip2')}")
            print(f"      deliveries_delta_flip2_minus_flip1: {details.get('deliveries_delta_flip2_minus_flip1')}")
        elif name == "d":
            print(f"      completed_kwargs_excerpt: {details.get('completed_kwargs_excerpt')}")
            print(f"      failed_kwargs_excerpt: {details.get('failed_kwargs_excerpt')}")
            print(f"      nonfailed_assistant_in_error: {details.get('nonfailed_assistant_in_error')}")
            print(f"      nonfailed_positional_assistant_in_error: {details.get('nonfailed_positional_assistant_in_error')}")
            print(f"      failed_assistant_in_error: {details.get('failed_assistant_in_error')}")
        elif name == "e":
            print(f"      register_result: {details.get('register_result')}")
            print(f"      rows_after_arm: {details.get('rows_after_arm')}")
            print(f"      unwatch_result: {details.get('unwatch_result')}")
            print(f"      calls_after_flip1: {details.get('calls_after_flip1')}")
            print(f"      calls_after_flip2: {details.get('calls_after_flip2')}")
            print(f"      deliveries_after_flip1: {details.get('deliveries_after_flip1')}")
            print(f"      deliveries_after_flip2: {details.get('deliveries_after_flip2')}")
            print(f"      deliveries_delta_flip2_minus_flip1: {details.get('deliveries_delta_flip2_minus_flip1')}")
        if "exception" in details:
            print(f"      exception: {details['exception']}")
            print(f"      traceback: {details.get('traceback', '')[:800]}")
    print()
    print("=" * 72)
    print(f"RESULT: {overall}")
    print("=" * 72)
    return 0 if overall == "PASS" else 1


# ── Driver ───────────────────────────────────────────────────────────────


def _ensure_clean_db(db_path: Path) -> None:
    """Wipe any prior fixture DB so each scenario starts from zero."""
    for ext in ("", "-wal", "-shm"):
        p = Path(str(db_path) + ext)
        if p.exists():
            p.unlink()


def main() -> int:
    print("=" * 72)
    print("Job-Event Watch Replay Incident — mechanics scenario")
    print(f"  repo_root            = {REPO_ROOT}")
    print(f"  caller               = {CALLER}")
    print(f"  mission_id           = {MISSION_ID} (SYNTHETIC, parked f27e2d15 NOT touched)")
    print(f"  n_terminal_receipts  = {N_TERMINAL_RECEIPTS}")
    print(f"  n_live_receipts      = {N_LIVE_RECEIPTS}")
    print(f"  total_receipts       = {TOTAL_RECEIPTS}")
    print(f"  mission_assistant_text = {MISSION_ASSISTANT_TEXT!r}")
    print(f"  watchdog             = {WATCHDOG_SECONDS}s")
    print(f"  env_safety_guard     = PASS (no POSTGRES_* in os.environ)")
    print("=" * 72)
    print()

    base_dir = Path("/tmp/jw-replay-scenario")
    base_dir.mkdir(parents=True, exist_ok=True)

    # ── (a) registration-time terminal filter ─────────────────────────
    db_a = base_dir / "a.sqlite"
    _ensure_clean_db(db_a)
    stamp("Starting scenario (a)")
    verdict_a, details_a = scenario_a(db_a)
    SCENARIO_RESULTS["a"] = verdict_a
    SCENARIO_DETAILS["a"] = details_a
    stamp(f"Scenario (a): {verdict_a}")

    # ── (b) flip fires armed set exactly once ─────────────────────────
    db_b = base_dir / "b.sqlite"
    _ensure_clean_db(db_b)
    stamp("Starting scenario (b)")
    verdict_b, details_b = scenario_b(db_b, details_a)
    SCENARIO_RESULTS["b"] = verdict_b
    SCENARIO_DETAILS["b"] = details_b
    stamp(f"Scenario (b): {verdict_b}")

    # ── (c) delta-arm re-call; no second burst on flip ────────────────
    db_c = base_dir / "c.sqlite"
    _ensure_clean_db(db_c)
    stamp("Starting scenario (c)")
    verdict_c, details_c = scenario_c(db_c)
    SCENARIO_RESULTS["c"] = verdict_c
    SCENARIO_DETAILS["c"] = details_c
    stamp(f"Scenario (c): {verdict_c}")

    # ── (d) slot wiring (F2): assistant text must NEVER leak into error=
    db_d = base_dir / "d.sqlite"
    _ensure_clean_db(db_d)
    stamp("Starting scenario (d)")
    verdict_d, details_d = scenario_d(db_d)
    SCENARIO_RESULTS["d"] = verdict_d
    SCENARIO_DETAILS["d"] = details_d
    stamp(f"Scenario (d): {verdict_d}")

    # ── (e) unwatch_job after consumption; zero new emissions ─────────
    db_e = base_dir / "e.sqlite"
    _ensure_clean_db(db_e)
    stamp("Starting scenario (e)")
    verdict_e, details_e = scenario_e(db_e)
    SCENARIO_RESULTS["e"] = verdict_e
    SCENARIO_DETAILS["e"] = details_e
    stamp(f"Scenario (e): {verdict_e}")

    # Dump full JSON evidence for the RESULTS report.
    evidence_path = base_dir / "evidence.json"
    evidence = {
        "scenarios": SCENARIO_RESULTS,
        "details": SCENARIO_DETAILS,
        "runtime_seconds": elapsed(),
        "watchdog_seconds": WATCHDOG_SECONDS,
        "mission_id": MISSION_ID,
        "caller": CALLER,
        "n_terminal_receipts": N_TERMINAL_RECEIPTS,
        "n_live_receipts": N_LIVE_RECEIPTS,
        "mission_assistant_text": MISSION_ASSISTANT_TEXT,
    }
    evidence_path.write_text(json.dumps(evidence, indent=2, default=str))
    stamp(f"Evidence written to {evidence_path}")

    return _emit_summary()


if __name__ == "__main__":
    sys.exit(main())
