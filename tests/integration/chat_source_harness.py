"""Shared harness helpers for the chat-source-worker-lane Phase 3 tests.

Importable helpers for any ``tests/integration/test_chat_source_*.py``
file. Lane-flag isolation is provided by the shared
:func:`chat_lane_flag_reset_fixture` autouse fixture factory — each
test module takes one module-level assignment (per project test
discipline: module-global state must not leak between tests).

The harness provides:

  * **Engine factory** — :func:`build_chat_source_engine` builds a
    file-backed SQLite with ``NullPool`` + WAL + busy_timeout=10000
    + ``PRAGMA case_sensitive_like = ON`` (F7/F9 review pins; the
    LIKE pragma is conditional on ``case_sensitive_like=True`` —
    wiring-only harnesses pass ``False``).

  * **Manager factory** — :func:`build_live_pool_manager` constructs
    a real ``InstanceManager`` with BOTH pools running. The chat
    pool is fixed at ``CHAT_WORKER_POOL_SIZE=2``; the default pool
    is sized via the ``num_workers`` kwarg (default
    ``WORKER_POOL_SIZE=5`` so Task #1 can saturate it). The
    ``USE_WORKER_POOL=false`` kill-switch is exercised via the
    ``use_worker_pool`` kwarg; the env var is restored on
    fixture teardown. After ``setup_worker_pool`` returns (non-
    kill-switch path), the factory DRAINS the default pool's
    gateless boot claims via
    :func:`wait_default_pool_boot_claims_drained` and asserts
    ``is_chat_lane_active()`` — callers may seed immediately
    (see that function's docstring for the adjudicated
    boot-window rationale).

  * **Seeding helpers** — :func:`seed_chat_message` writes a
    ``MessageQueue`` + ``Task`` pair with a realistic production-
    shaped source (``telegram:alice:1`` style). :func:`fetch_task_by_work_id`
    / :func:`fetch_pending_tasks` / :func:`fetch_running_tasks` /
    :func:`fetch_message_queue` read back state for assertions.

  * **Mock run_task factories** — :func:`make_blocking_run_task`,
    :func:`make_short_run_task`, :func:`make_no_op_run_task`. Tests
    drop these onto ``manager._task_processor.run_task`` to control
    task execution timing (block for saturation, short completion
    for queueing tests, no-op for kill-switch).

This module is intentionally small — it does NOT pull in
``WorkResolverService`` / ``JobProcessor`` / ``DispatchEventBus``.
Those are out-of-scope for the lane-routing + lifecycle assertions
that Phase 3 makes; the saturation / non-inheritance / kill-switch /
boot-line tests do not need the full job-stack seam, and the
simpler harness keeps the timing assertions deterministic.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
from daemon.constants import CHAT_WORKER_POOL_SIZE, WORKER_POOL_SIZE


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Engine factory — file-backed SQLite, NullPool, WAL, case_sensitive_like
# ---------------------------------------------------------------------------


def build_chat_source_engine(
    db_path: str, *, case_sensitive_like: bool = True
) -> Engine:
    """Build a file-backed SQLite engine for chat-source tests.

    ``NullPool`` + per-connection ``connect`` listener PRAGMA is the
    convention for chat-source-worker-lane integration tests (F7 —
    file-backed SQLite, not in-memory). WAL keeps multi-checkout
    concurrency honest. ``PRAGMA case_sensitive_like = ON`` (F9)
    keeps the ``source LIKE 'telegram:%'`` predicate case-SENSITIVE
    for any test that exercises the claim seam — matching production
    PG semantics. Pass ``case_sensitive_like=False`` for wiring-only
    harnesses that never exercise the LIKE claim predicate (parity
    with the pre-consolidation inline engine fixtures).
    """
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(eng, "connect")
    def _enable_pragmas(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        if case_sensitive_like:
            # F9 — PG-parity for the lane predicate's LIKE semantics.
            cursor.execute("PRAGMA case_sensitive_like = ON")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    return eng


# ---------------------------------------------------------------------------
# Manager factory — production wiring, no LLM
# ---------------------------------------------------------------------------


def _build_config():
    """Build the canonical Config object for chat-source tests.

    Same shape as the Phase 2 wiring test — keeps the test surface
    hermetic. ``db_path=\":memory:\"`` is harmless because every
    engine is created from the patched ``create_engine_from_config``
    (returned ``engine`` is the file-backed SQLite from the test
    fixture).
    """
    from daemon.config import (
        AgentsConfig,
        Config,
        DaemonConfig,
        LLMConfig,
        LimitsConfig,
        PersistenceConfig,
    )

    return Config(
        llm=LLMConfig(
            base_url="https://api.openai.com/v1",
            api_key="test-key",
            model="gpt-4",
            temperature=0.7,
        ),
        limits=LimitsConfig(
            max_children_per_instance=3,
            instance_timeout_minutes=60,
        ),
        persistence=PersistenceConfig(
            db_path=":memory:",
            checkpoint_interval=1,
            checkpoint_ttl_hours=168,
            checkpoint_cleanup_interval=24,
            max_instance_history=300,
        ),
        daemon=DaemonConfig(host="127.0.0.1", port=8079),
        agents=AgentsConfig(directory="./agents"),
    )


# ---------------------------------------------------------------------------
# Boot-claim drain barrier (Phase 3 round-2 adjudication fix)
# ---------------------------------------------------------------------------


def wait_default_pool_boot_claims_drained(
    manager,
    *,
    num_workers: int,
    timeout_s: float = 10.0,
) -> None:
    """Deterministically drain the default pool's boot-window claims.

    Adjudicated root cause of the Phase 3 ``xfail`` class (2026-09-19,
    instrumented per-claim flag capture + 2ms flag-monitor thread; full
    narrative in ``tests/integration/test_chat_source_lane_saturation.py``
    module docstring): ``InstanceManager.setup_worker_pool`` starts the
    DEFAULT pool BEFORE it calls ``set_chat_lane_active(True)``
    (``daemon/manager.py:6798``, B1 boot ordering — the flag must flip
    only after the chat pool constructs so a construction failure
    leaves fail-open). Every default worker therefore performs its
    FIRST claim attempt with the flag still False (fail-open, gateless
    SQL). If a test seeds chat rows in the few milliseconds after
    ``setup_worker_pool`` returns, a gateless boot claim still in
    flight (delayed by GIL/SQLite lock contention behind the seeding
    transactions) can execute its UPDATE onto a freshly committed
    chat row — ``worker-N`` claims a ``telegram:`` row. The lane
    predicate itself is correct: every observed mis-claim composed
    with ``flag=False`` (pre-flip), never mid-test.

    This barrier waits until every default worker has COMPLETED its
    boot claim attempt, so any gateless composition has fully retired
    before the caller seeds. Completion is observed via the pool's
    own stats: each worker's run() loop begins with exactly one claim
    attempt and cannot re-enter claim until it completes, and no
    ``notify_work()`` has fired yet (the caller notifies only after
    seeding), so within the ms-scale barrier window the pool's
    ``empty_claim_attempts + Σ(worker.tasks_claimed)`` counts exactly
    the completed boot attempts. Reaching ``>= num_workers`` proves
    every worker's boot claim returned.

    Not a production fix: in production the boot window is the
    DESIGNED B1 fail-open state (the chat pool does not exist yet;
    processing a chat row on the default lane during that window is
    the pre-lane behavior and nothing is stranded). The harness must
    simply not seed INTO the boot window.
    """
    pool = manager._worker_pool
    if pool is None:
        raise RuntimeError(
            "wait_default_pool_boot_claims_drained: default pool is None "
            "(USE_WORKER_POOL=false?) — cannot drain boot claims"
        )
    deadline = time.monotonic() + timeout_s
    completed = -1
    while time.monotonic() < deadline:
        stats = pool.get_stats()
        completed = stats.get("empty_claim_attempts", 0) + sum(
            w.get("tasks_claimed", 0) for w in stats.get("workers", [])
        )
        if completed >= num_workers:
            return
        time.sleep(0.005)
    raise RuntimeError(
        f"default pool boot claims did not drain within {timeout_s}s "
        f"(completed boot attempts={completed}, needed>={num_workers}) — "
        f"boot-window gateless claims may still be in flight"
    )


@contextmanager
def wire_manager_only(
    engine: Engine, *, use_worker_pool: str | None = None
) -> Iterator:
    """Build a real ``InstanceManager`` WITHOUT starting any pool —
    the wiring-only seam shared by the chat-source test files.

    The LLM is irrelevant (wiring-only); ``build_instance_graph`` is
    patched to a sentinel so the manager can be constructed without
    the full graph stack. Pool construction is the CALLER's job:
    tests that need explicit control call
    ``manager.setup_worker_pool(...)`` inside the with-block, and
    :func:`build_live_pool_manager` delegates here before running
    its own ``setup_worker_pool`` + boot-claim drain.

    Skip ``manager.initialize()`` (heavy async lifespan) and stub
    the only seam ``setup_worker_pool`` touches on the manager's
    pre-init state — ``self._maintenance_service`` is set by
    ``initialize()`` but ``setup_worker_pool`` calls
    ``self._maintenance_service.set_task_repository(...)`` at
    manager.py:6473. Same recipe as
    ``tests/integration/test_wc_wake_pure_hang.py:406-409``.

    Args:
        engine: The shared engine injected into every
            ``create_engine_from_config`` call inside the manager.
        use_worker_pool: ``"false"`` to set the kill-switch; ``None``
            (default) clears it so both pools construct.
    """
    # Set/clear the USE_WORKER_POOL env flag WITHOUT polluting the
    # process environment for other tests — teardown restores the
    # default and other tests in this module use the same pattern.
    if use_worker_pool is not None:
        os.environ["USE_WORKER_POOL"] = use_worker_pool
    else:
        os.environ.pop("USE_WORKER_POOL", None)

    try:
        from unittest.mock import patch

        from daemon.manager import InstanceManager
        from daemon.services.maintenance import MaintenanceService

        config = _build_config()

        with (
            patch(
                "daemon.migrations.runner.MigrationRunner.run_pending_migrations",
                return_value=[],
            ),
            patch(
                "daemon.manager.create_engine_from_config",
                return_value=engine,
            ),
            patch(
                "daemon.manager.build_instance_graph",
                return_value=None,
            ),
        ):
            manager = InstanceManager(config)
            # Stub the maintenance service — ``initialize()`` is the
            # production site but is heavy (async lifespan). The
            # only seam ``setup_worker_pool`` touches is
            # ``set_task_repository(...)``; a bare ``MaintenanceService``
            # instance + a request-registry dict suffices. Mirror
            # the wake_harness recipe at
            # ``tests/integration/test_wc_wake_pure_hang.py:406-409``.
            manager._maintenance_service = MaintenanceService()
            manager._maintenance_service.set_request_registry({})
            yield manager
            # Safety net: ensure pools are torn down even if the
            # caller forgets — prevents leaked worker threads across
            # tests. Fail-LOUD: the broad except width is deliberate
            # (teardown must never mask the test's own outcome), but
            # the previous silence hid real shutdown failures.
            try:
                manager.shutdown_worker_pool()
            except Exception:
                logger.warning(
                    "teardown safety-net swallowed "
                    "shutdown_worker_pool exception",
                    exc_info=True,
                )
    finally:
        os.environ.pop("USE_WORKER_POOL", None)


@contextmanager
def build_live_pool_manager(
    engine: Engine,
    *,
    num_workers: int = WORKER_POOL_SIZE,
    use_worker_pool: str | None = None,
) -> Iterator:
    """Build a real ``InstanceManager`` with both pools running.

    Mirrors the production ``setup_worker_pool`` shape; the LLM is
    irrelevant (wiring + claim-only); ``build_instance_graph`` is
    patched to a sentinel so the manager can be constructed without
    the full graph stack.

    The chat pool is constructed at ``CHAT_WORKER_POOL_SIZE=2``; the
    default pool is sized by ``num_workers`` (default
    ``WORKER_POOL_SIZE=5``). The test that needs 5 default-worker
    saturation passes ``num_workers=WORKER_POOL_SIZE`` explicitly
    (note (i) in the plan).

    Args:
        engine: The shared file-backed SQLite engine injected into
            every ``create_engine_from_config`` call.
        num_workers: Size of the DEFAULT worker pool. The chat pool
            is always ``CHAT_WORKER_POOL_SIZE=2`` (override that
            requires touching the constant — not supported here).
        use_worker_pool: ``"false"`` to set the kill-switch;
            ``None`` (default) clears it so both pools construct.

    Yields:
        The configured ``InstanceManager`` (real pools, stubbed
        ``run_task``-free task processor; tests inject mocks via
        ``manager._task_processor.run_task = ...``).
    """
    with wire_manager_only(
        engine, use_worker_pool=use_worker_pool
    ) as manager:
        manager.setup_worker_pool(num_workers=num_workers)
        if use_worker_pool in ("false", "0", "no"):
            # Kill-switch path: no pools exist, flag stays False
            # (fail-open) — nothing to drain and the assert below
            # MUST NOT hold (that is the kill-switch contract).
            yield manager
        else:
            # Adjudication fix (2026-09-19): deterministically
            # retire the default pool's gateless boot claims
            # BEFORE yielding to the caller, so tests never seed
            # into the B1 boot window. Belt: the flag must
            # already be True here.
            wait_default_pool_boot_claims_drained(
                manager, num_workers=num_workers
            )
            from daemon.repositories.task.repository import (
                is_chat_lane_active,
            )

            assert is_chat_lane_active(), (
                "chat lane flag must be True after setup_worker_pool "
                "— kill-switch early-return or teardown raced the setup"
            )
            yield manager


# ---------------------------------------------------------------------------
# Task enqueueing helpers — direct DB insert (avoids the full HTTP/job
# stack). Tests seed ``MessageQueue`` + ``Task`` rows with realistic
# production-shaped source values, then call ``manager._notify_all_pools()``
# to wake the workers.
# ---------------------------------------------------------------------------


def _now_utc_naive() -> datetime:
    from daemon.services.timestamps import now_utc_naive

    return now_utc_naive()


def seed_chat_message(
    engine: Engine,
    *,
    instance_id: str,
    source: str,
    content: str = "hello from chat",
    created_at: datetime | None = None,
    work_id: str | None = None,
    message_id: str | None = None,
) -> tuple[str, str]:
    """Insert a PENDING ``MessageQueue`` + ``Task`` row pair with the
    given source. Returns ``(message_id, work_id)``.

    Realistic production-shaped mint values per F1 review pin (the
    registry always appends ``:<external_user_id>``; bare
    ``"telegram:"`` is the deprecated stub shape).

    The created Task row is the claim target. ``created_at`` defaults
    to ``now_utc_naive()`` — pass an explicit value when ordering
    matters.
    """
    import uuid

    from daemon.repositories.message_queue.models import MessageQueue
    from daemon.repositories.task.models import Task, TaskType
    from daemon.repositories.instance.models import Instance

    if message_id is None:
        message_id = f"msg-{uuid.uuid4().hex[:12]}"
    if work_id is None:
        work_id = f"work-{uuid.uuid4().hex[:12]}"
    if created_at is None:
        created_at = _now_utc_naive()

    # Ensure instance row exists (FK target). Idempotent — no-op if
    # the row is already present.
    with Session(engine) as s:
        existing = s.get(Instance, instance_id)
        if existing is None:
            s.add(
                Instance(
                    instance_id=instance_id,
                    agent_id="ari",
                    agent_dir="/agents/ari",
                    status="idle",
                )
            )
            s.commit()

    with Session(engine) as s:
        s.add(
            MessageQueue(
                message_id=message_id,
                instance_id=instance_id,
                content=content,
                source=source,
                # NOTE: no explicit enqueued_at — the model has a
                # default_factory that produces naive-UTC digits
                # (DC-A fix). Passing an aware datetime here would
                # render in the session TimeZone and store local
                # digits (the SQLite harness has no session TZ,
                # so the default is fine).
            )
        )
        s.add(
            Task(
                work_id=work_id,
                task_type=TaskType.PROCESS_MESSAGE.value,
                instance_id=instance_id,
                message_id=message_id,
                status="pending",
                created_at=created_at,
            )
        )
        s.commit()
    return message_id, work_id


def fetch_task_by_work_id(engine: Engine, work_id: str):
    """Read a Task row back by ``work_id`` (None if not found)."""
    from daemon.repositories.task.models import Task
    from sqlmodel import select

    with Session(engine) as s:
        return s.exec(select(Task).where(Task.work_id == work_id)).first()


def fetch_pending_tasks(engine: Engine, *, limit: int | None = None):
    """Return all PENDING tasks (FIFO by ``created_at`` ASC)."""
    from daemon.repositories.task.models import Task, TaskStatus
    from sqlmodel import select

    with Session(engine) as s:
        stmt = (
            select(Task)
            .where(Task.status == TaskStatus.PENDING.value)
            .order_by(Task.created_at.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(s.exec(stmt).all())


def fetch_running_tasks(engine: Engine, *, limit: int | None = None):
    """Return all RUNNING tasks (FIFO by ``created_at`` ASC)."""
    from daemon.repositories.task.models import Task, TaskStatus
    from sqlmodel import select

    with Session(engine) as s:
        stmt = (
            select(Task)
            .where(Task.status == TaskStatus.RUNNING.value)
            .order_by(Task.created_at.asc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(s.exec(stmt).all())


def fetch_message_queue(engine: Engine, message_id: str):
    """Read a ``MessageQueue`` row back (None if not found)."""
    from daemon.repositories.message_queue.models import MessageQueue

    with Session(engine) as s:
        return s.get(MessageQueue, message_id)


# ---------------------------------------------------------------------------
# Mock run_task factories — tests drop these onto manager._task_processor.run_task
# ---------------------------------------------------------------------------


def make_blocking_run_task(
    *,
    block_evt: threading.Event | None = None,
    release_evt: threading.Event | None = None,
    sleep_seconds: float | None = None,
    record_to: list | None = None,
):
    """Build a ``run_task`` replacement that BLOCKS until released.

    The returned function has signature ``run_task(task, cancellation_token=None)``
    — drop it onto ``manager._task_processor.run_task``. The worker
    thread enters run_task, optionally records the call, then waits
    on ``release_evt`` (or sleeps). When released it returns normally
    (the worker increments ``_tasks_completed`` and loops back to
    claim the next pending task — the saturation window stays open
    while we hold the release).

    Args:
        block_evt: if set, the run_task blocks until this event is set.
            Useful for "wait until claim is confirmed, then start
            holding the lane open". For most saturation tests, omit
            ``block_evt`` and use only ``release_evt``.
        release_evt: when this event is set, run_task returns.
        sleep_seconds: alternative to ``release_evt`` — sleep for this
            many seconds (no event needed).
        record_to: append ``task.work_id`` to this list on entry
            (useful for asserting which tasks claimed).

    Returns:
        A callable suitable for ``manager._task_processor.run_task``.
    """
    def _run_task(task, cancellation_token=None):
        if record_to is not None:
            record_to.append(task.work_id)
        if block_evt is not None:
            block_evt.wait(timeout=30.0)
        if release_evt is not None:
            release_evt.wait(timeout=30.0)
        elif sleep_seconds is not None:
            time.sleep(sleep_seconds)

    return _run_task


def make_short_run_task(
    *,
    sleep_seconds: float = 0.05,
    record_to: list | None = None,
):
    """Build a ``run_task`` replacement that completes quickly.

    Used for the chat-lane saturation test (Task #4) — the first two
    chat tasks MUST complete in ≤1s so the 3rd-message pickup
    timing measures queueing latency, not task duration (D10.2 /
    Task #4a fixture-validation gate).
    """
    def _run_task(task, cancellation_token=None):
        if record_to is not None:
            record_to.append(task.work_id)
        time.sleep(sleep_seconds)

    return _run_task


def make_no_op_run_task(record_to: list | None = None):
    """Build a ``run_task`` replacement that returns immediately.

    Used for the kill-switch test — workers should NOT claim tasks
    at all when ``USE_WORKER_POOL=false``; the no-op shape makes the
    "claim then complete" path verifiable on the live (non-kill-
    switch) pool.
    """
    def _run_task(task, cancellation_token=None):
        if record_to is not None:
            record_to.append(task.work_id)

    return _run_task


def wait_until(predicate, *, timeout: float, interval: float = 0.02) -> bool:
    """Poll ``predicate`` until it returns truthy or ``timeout``.

    Returns ``True`` if the predicate became truthy, ``False`` on
    timeout (caller decides what to assert).
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


# Lane-flag reset — shared autouse fixture factory. Per project
# discipline module-global state must not leak between tests; each
# test module takes ONE module-level assignment instead of re-declaring
# the same inline fixture.
def chat_lane_flag_reset_fixture():
    """Return an autouse fixture that hard-resets the B1 module flag
    around every test in the calling module.

    The ``@pytest.fixture(autouse=True)`` decoration travels with the
    returned function object, so a module-level alias is all the
    wiring a test file needs::

        from tests.integration.chat_source_harness import (
            chat_lane_flag_reset_fixture,
        )

        chat_lane_flag_reset = chat_lane_flag_reset_fixture()
    """
    from daemon.repositories.task.repository import set_chat_lane_active

    @pytest.fixture(autouse=True)
    def _reset_chat_lane_flag():
        set_chat_lane_active(False)
        yield
        set_chat_lane_active(False)

    return _reset_chat_lane_flag


__all__ = [
    "CHAT_WORKER_POOL_SIZE",
    "WORKER_POOL_SIZE",
    "build_chat_source_engine",
    "build_live_pool_manager",
    "chat_lane_flag_reset_fixture",
    "seed_chat_message",
    "fetch_task_by_work_id",
    "fetch_pending_tasks",
    "fetch_running_tasks",
    "fetch_message_queue",
    "make_blocking_run_task",
    "make_short_run_task",
    "make_no_op_run_task",
    "wait_until",
    "wire_manager_only",
]
