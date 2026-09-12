"""Phase 3 integration + acceptance tests — ``generate_chart`` charter reuse.

Real-dispatch revive walk on a file-backed SQLite engine. Mirrors the
acceptance-walk convention of ``tests/test_governor_recursion_acceptance_walk.py``:
real ``SQLModelInstanceRepository`` + real ``CompletionRegistry`` singleton +
REAL manager facade surface wired to a stubbed ``enqueue_message`` that does
the actual terminal→RUNNING DB revive (the load-bearing seam) without spinning
up the worker pool.

Coverage (per ``.agents/shared/planning/generate-chart-charter-reuse/
phase3-plan.md``):

* **T1** Real-dispatch revive walk — discover → revive via real enqueue →
  registry.wait_for → same instance id, no second spawn.
* **T2** Budget & lifecycle non-interaction — ``get_agent_tool_revive_count``
  stays 0 across COMPLETED / ERROR / TERMINATED-cycle revives; spawn-cap
  headroom is not consumed by reuse.
* **T3** ERROR/FAILED policy end-to-end — 1st call revives, 2nd call respawns.
* **T5** Fan-out scoping — sibling callers → distinct charters; same caller →
  same charter id, even across caller-level terminal→revive.
* **T6** Facade-forwarding verification — no new kwargs on ``enqueue_message``
  in the Phase 1 diff.

T7 (compaction canary) is OPTIONAL and skipped per the plan ("skip without
ceremony if the fixture budget is tight").

MOCK FIDELITY: ``invoke_agent_and_wait`` is the tripwire (MUST NOT be awaited
on reuse); ``enqueue_message`` does the real DB revive (terminal→RUNNING)
but does not process the Task — there is no worker pool here. The completion
flow is driven by a side ``asyncio`` task that calls ``registry.complete(...)``
with canned output. The ``instances`` + ``instance_hierarchy`` writes are
REAL via ``repository.create`` (the DB half of the spawn path), bypassing the
prompt-cache / tool-factory / LangGraph-graph tail that ``spawn_instance``
also performs (irrelevant to reuse mechanics — the harness rows exercise the
discovery and revive paths exactly as production does).
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, create_engine

import daemon.tools.chart_tools as chart_tools_module
from daemon.repositories.instance.models import Instance, InstanceHierarchy
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.services.completion_registry import (
    CompletionRegistry,
    get_completion_registry,
)
from daemon.write_pause_guard import WritePauseGuard


# ──────────────────────────────────────────────────────────────────────────────
# Logging helper — single mode log format is greppable (Phase 1 invariant).
# ──────────────────────────────────────────────────────────────────────────────


def _mode_records(caplog) -> list:
    """All chart_tools log records captured by ``caplog``."""
    return [r for r in caplog.records if r.name == "daemon.tools.chart_tools"]


# ──────────────────────────────────────────────────────────────────────────────
# File-backed SQLite engine — plan: tmp_path + NullPool + WAL + busy_timeout.
# Mirrors the plan's explicit "never in-memory StaticPool" convention.
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path):
    """File-backed SQLite engine with the REAL instance/hierarchy tables.

    WAL + ``busy_timeout=30000`` keep multi-thread SQLAlchemy writes from
    racing each other under the lightweight test scheduler; ``NullPool`` keeps
    SQLAlchemy from sharing connections across threads (the in-memory
    StaticPool write-interleaving hazard is deliberately avoided).
    """
    db_path = tmp_path / "reuse_walk.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )

    # Enable WAL + busy_timeout on every new SQLite connection. These
    # pragmas are connection-local in SQLite, so they must be issued from
    # the connection's own cursor (not engine-level).
    @sa_event.listens_for(eng, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


# ──────────────────────────────────────────────────────────────────────────────
# REAL CompletionRegistry singleton — reset between tests so the singleton
# state never leaks (lazy-import gotcha — chart_tools re-binds every call,
# so the module attribute must be reset to None at the start of each test).
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_registry():
    """Fresh ``CompletionRegistry`` instance returned by ``get_completion_registry``.

    The chart-reuse helper imports ``get_completion_registry`` lazily inside
    ``_reuse_charter``; the lazy import re-binds on every call, so the
    module-attribute reset below is the documented invalidation knob
    (same pattern as ``tests/test_finalize_instance.py:116-125``).
    """
    import daemon.services.completion_registry as cr_module

    new_registry = CompletionRegistry()
    with patch.object(cr_module, "_completion_registry", new=new_registry):
        with patch(
            "daemon.services.completion_registry.get_completion_registry",
            return_value=new_registry,
        ):
            yield new_registry


# ──────────────────────────────────────────────────────────────────────────────
# Reuse-module state — keep tests independent of one another's leftover
# in-flight / counter state.
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_reuse_module_state():
    chart_tools_module._inflight_reuse.clear()
    chart_tools_module._reuse_revive_attempts.clear()
    yield
    chart_tools_module._inflight_reuse.clear()
    chart_tools_module._reuse_revive_attempts.clear()


# ──────────────────────────────────────────────────────────────────────────────
# Charter / caller row helpers — REAL DB writes via SQLModel ORM.
# ──────────────────────────────────────────────────────────────────────────────


def _iso(ts: datetime) -> str:
    return ts.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _seed_instance(
    engine,
    *,
    instance_id: str,
    agent_id: str = "worker",
    parent_id: str | None = None,
    status: str = "running",
    metadata: dict | None = None,
    last_activity_at: datetime | None = None,
    created_at: datetime | None = None,
    project_id: str | None = None,
) -> Instance:
    """Insert a REAL Instance row.

    Honors ``invoked_as_tool`` stamping (caller's charter child must carry
    it for the chart reuse discovery to find it; see
    ``instance_lifecycle.py:1798-1799``).
    """
    now = datetime.now(timezone.utc)
    row = Instance(
        instance_id=instance_id,
        agent_id=agent_id,
        agent_dir=f"./agents/{agent_id}",
        parent_id=parent_id,
        status=status,
        instance_metadata=dict(metadata or {}),
        last_activity_at=last_activity_at,
        created_at=_iso(created_at or now),
        updated_at=_iso(now),
        project_id=project_id,
        version=1,
    )
    with Session(engine) as session:
        session.add(row)
        if parent_id is not None:
            session.add(
                InstanceHierarchy(
                    parent_id=parent_id,
                    child_id=instance_id,
                    created_at=_iso(now),
                )
            )
        session.commit()
        session.refresh(row)
    return row


def _seed_charter_child(
    engine,
    *,
    charter_id: str,
    parent_id: str,
    status: str = "completed",
    last_activity_at: datetime | None = None,
    created_at: datetime | None = None,
    with_hierarchy: bool = False,
) -> Instance:
    """Seed a charter child row visible to ``_find_reusable_charter``.

    ``invoked_as_tool=True`` is stamped (the discovery filter at
    ``chart_tools.py:_find_reusable_charter`` requires it). By default,
    no ``instance_hierarchy`` row is inserted — the production path deletes
    the hierarchy row when a child completes
    (``child_reports.py``/``error_reporting.py`` cleanup), so a COMPLETED
    charter is invisible to ``count_children``. Pass ``with_hierarchy=True``
    only when the test specifically needs to mimic an ACTIVE child
    (e.g., the T2 cap-fill leg).
    """
    row = _seed_instance(
        engine,
        instance_id=charter_id,
        agent_id="charter",
        parent_id=parent_id,
        status=status,
        metadata={"invoked_as_tool": True},
        last_activity_at=last_activity_at,
        created_at=created_at,
    )
    if not with_hierarchy:
        # Drop the hierarchy row that ``_seed_instance`` inserted alongside
        # the parent_id — production deletes it on completion.
        with Session(engine) as session:
            link = session.get(InstanceHierarchy, (parent_id, charter_id))
            if link is not None:
                session.delete(link)
                session.commit()
    return row


def _set_charter_status(engine, charter_id: str, status: str) -> None:
    """Direct ORM update on a charter row (mirrors the T1 "force terminal"
    step in the plan — terminal status is what the revive path consumes).
    """
    with Session(engine) as session:
        row = session.get(Instance, charter_id)
        assert row is not None, f"charter {charter_id} missing"
        row.status = status
        session.add(row)
        session.commit()


def _read_charter_status(engine, charter_id: str) -> str | None:
    with Session(engine) as session:
        row = session.get(Instance, charter_id)
        return row.status if row is not None else None


def _count_instance_rows(engine) -> int:
    with Session(engine) as session:
        return len(list(session.exec(Instance.__table__.select())))


def _count_hierarchy_rows(engine, parent_id: str | None = None) -> int:
    from sqlalchemy import select

    stmt = select(InstanceHierarchy)
    if parent_id is not None:
        stmt = stmt.where(InstanceHierarchy.parent_id == parent_id)
    with Session(engine) as session:
        return len(list(session.exec(stmt)))


# ──────────────────────────────────────────────────────────────────────────────
# REAL-DISPATCH enqueue_message — does the actual terminal→RUNNING revive via
# real DB writes (the load-bearing seam), but does NOT spawn a worker. The
# completion flow is driven by ``_complete_charter_in_background`` below.
# ──────────────────────────────────────────────────────────────────────────────


def _build_enqueue_message(
    engine: object,
    write_guard: WritePauseGuard,
    revive_flips: list | None = None,
):
    """Build an ``enqueue_message`` coroutine that does the REAL revive seam.

    Mirrors the status-flip half of ``InstanceMessagingService.
    _prepare_enqueued_message`` at ``instance_messaging.py:1931-1958``:
    terminal→RUNNING via a real ``WriteGuardSession`` transaction. No Task
    row is inserted and no worker is notified — the side task
    (``_complete_charter_in_background``) is the only thing that signals
    the charter's completion to the ``CompletionRegistry``. This is
    intentional and faithful to the reuse seam: the discovery/revive path
    under test is the COMPLETED→RUNNING→terminal flip, not the worker-
    pool dispatch tail.

    ``revive_flips`` (optional) is a list that, when provided, gets
    appended ``(instance_id, prev_status, "running")`` for every flip
    the stub actually performs. Tests use this to pin the mid-flight
    terminal→RUNNING transition (it is recorded inside ``_do_revive``
    before the side task re-flips the row terminal).
    """

    from daemon.repositories.instance.models import InstanceStatus
    from daemon.write_pause_guard import WriteGuardSession

    TERMINAL_STATUSES = {
        InstanceStatus.COMPLETED.value,
        InstanceStatus.TERMINATED.value,
        InstanceStatus.ERROR.value,
        InstanceStatus.FAILED.value,
    }

    async def enqueue_message(
        *,
        instance_id: str,
        message: str,
        source: str = "api",
        priority: int = 1,
        images=None,
        metadata=None,
        is_deferred: bool = False,
        is_background: bool = False,
        work_id=None,
        work_id_required: bool = False,
    ):
        # Yield once so the caller has a chance to register the wait_for
        # BEFORE the row flip — keeps the buffered-completion race
        # deterministic (mirrors ``completion_registry.py:79-85``).
        await asyncio.sleep(0)
        # REAL terminal→RUNNING revive — the load-bearing assertion seam.
        def _do_revive() -> str:
            with WriteGuardSession(Session(engine), write_guard) as session:
                row = session.get(Instance, instance_id)
                if row is None:
                    return "missing"
                prev = row.status
                if (
                    prev in TERMINAL_STATUSES
                    or prev in (
                        InstanceStatus.IDLE.value,
                        InstanceStatus.WAITING_CHILDREN.value,
                    )
                ) and prev != InstanceStatus.PAUSED.value:
                    row.status = InstanceStatus.RUNNING.value
                    row.last_activity_at = datetime.now(timezone.utc)
                    session.add(row)
                    session.commit()
                    # Record the mid-flight flip on the harness-exposed list
                    # BEFORE returning, so tests can pin the terminal→RUNNING
                    # transition at enqueue time (not just the final terminal
                    # state). This is the seam that makes the T1 mid-flight
                    # pin non-vacuous: if the stub never reached this point,
                    # the list stays empty and the assertion fails.
                    if revive_flips is not None:
                        revive_flips.append((instance_id, prev, "running"))
                return prev

        prev_status = await asyncio.to_thread(_do_revive)
        # Hand back a token with a ``message_id`` attribute — the test inspects
        # call args via the wrapping AsyncMock but does not read the result.
        return SimpleNamespace(
            message_id=f"msg-{instance_id[:8]}",
            instance_id=instance_id,
            status="queued",
            job_id=None,
            queued=False,
        )

    return enqueue_message


# ──────────────────────────────────────────────────────────────────────────────
# Harness manager — REAL repo, REAL completion_registry (via fresh_registry),
# REAL write_guard, REAL config. The messaging surface is wrapped via the
# REAL-dispatch enqueue_message above; everything else is stubbed at the
# facade.
# ──────────────────────────────────────────────────────────────────────────────


class _HarnessManager:
    """Bare class so attribute stubs don't go through MagicMock __init__."""


def build_harness_manager(
    engine,
    *,
    max_children_per_instance: int = 50,
):
    """Wire a manager facade suitable for the real-dispatch charter-reuse walk.

    REAL: ``engine``, ``write_guard``, ``_instance_repository``, ``config``,
    ``_messaging_service``-via-enqueue (the load-bearing seam).
    STUBBED: prompt cache, instances dict, live_hub, task_repo, get_agent_tool_*
    counters (the agent-tool ReviveGuard is intentionally inert for charter
    reuse — programmatic path; the assert in T2 verifies the counter stays 0).
    """
    from daemon.config import Config

    mgr = _HarnessManager()
    mgr.engine = engine
    mgr.write_guard = WritePauseGuard()
    mgr._instance_repository = SQLModelInstanceRepository(engine)
    mgr.config = Config()
    # ``LimitsConfig`` defaults to 50; for the T2 spawn-cap test we patch the
    # attribute in-place (REAL config attribute, not a mock — the lifecycle
    # cap check reads it directly).
    mgr.config.limits.max_children_per_instance = max_children_per_instance
    # Externals the chart tool does NOT touch but the harness keeps:
    mgr.prompt_cache = MagicMock()
    mgr.instances = {}
    mgr._live_hub = MagicMock()
    mgr._checkpointer = None
    mgr._compactor = None
    mgr._task_repo = MagicMock()
    mgr._project_repository = MagicMock()
    mgr._job_queue_service = MagicMock()
    mgr._worker_pool = MagicMock()
    mgr.shared_meta_kv_repo = MagicMock()
    mgr.message_metadata_repo = None
    mgr._request_registry = MagicMock()
    mgr._shutting_down = False
    mgr.spawn_instance = MagicMock()  # tripwire: reuse MUST NOT call this
    mgr.spawn_instance_with_mcp = AsyncMock(return_value=("unused", None))
    # Agent-tool ReviveGuard — must be inert for charter reuse (T2 assert).
    # REAL callables (NOT MagicMock) backed by the ``_agent_tool_revive_counts``
    # dict. If the production chart path ever starts calling
    # ``manager.note_agent_tool_revive``, the dict bumps and every existing
    # ``== 0`` pin (T2 ×4, T3 ×1) FAILS — that's what makes the pins
    # non-vacuous. The previous MagicMock was hard-wired to return 0 and
    # could not detect a regression.
    mgr._agent_tool_revive_counts = {}

    def _note_agent_tool_revive(instance_id: str, prior_status: str | None = None) -> int:
        # Semantic mirror of manager.py:2816-2895 (consume only when
        # ``prior_status`` is None or in {ERROR, FAILED}; non-consuming for
        # COMPLETED/TERMINATED). The chart-reuse path MUST never call this
        # — that's the T2 pin — so any future call is a regression signal.
        if prior_status is None or prior_status in ("error", "failed"):
            mgr._agent_tool_revive_counts[instance_id] = (
                mgr._agent_tool_revive_counts.get(instance_id, 0) + 1
            )
        return mgr._agent_tool_revive_counts[instance_id]

    def _get_agent_tool_revive_count(instance_id: str) -> int:
        return mgr._agent_tool_revive_counts.get(instance_id, 0)

    mgr.note_agent_tool_revive = _note_agent_tool_revive
    mgr.get_agent_tool_revive_count = _get_agent_tool_revive_count
    # Mid-flight flip recorder — ``_build_enqueue_message`` appends
    # ``(instance_id, prev, "running")`` for every terminal→RUNNING flip
    # the stub actually performs. T1 pins this list to prove the flip
    # happened at enqueue time (not just implied by wait_for resolving).
    mgr.revive_flips = []
    # REAL-dispatch enqueue_message (the load-bearing seam).
    enqueue = _build_enqueue_message(engine, mgr.write_guard, mgr.revive_flips)
    mgr.enqueue_message = AsyncMock(side_effect=enqueue)
    return mgr


# ──────────────────────────────────────────────────────────────────────────────
# Side-task helper — completes the registry entry after a real-enqueue flip.
# Mirrors the plan's "simulate the charter's turn by completing the registry
# entry from a side asyncio task" recipe.
# ──────────────────────────────────────────────────────────────────────────────


async def _complete_charter_after_enqueue(
    *,
    registry: CompletionRegistry,
    charter_id: str,
    repo: SQLModelInstanceRepository,
    engine,
    content: str = "```mermaid\ngraph TD\nA-->B\n```\n\nRefined.",
    is_error: bool = False,
    delay_seconds: float = 0.01,
    flip_back_to_terminal: str = "completed",
) -> None:
    """Side task: wait for the registry to be registered, complete it, flip
    the row back to terminal (simulating the real charter turn finishing).

    The ``delay_seconds`` lets the awaiting ``_reuse_charter`` coroutine
    reach ``registry.wait_for`` before we complete; the chart tool's own
    ``_reuse_charter`` handles the buffered-completion race explicitly at
    ``chart_tools.py:262-264`` (re-register after enqueue), so even zero
    delay is safe — but a tiny delay makes the test deterministic under
    different schedulers.
    """
    await asyncio.sleep(delay_seconds)
    registry.complete(charter_id, content, is_error=is_error)
    if flip_back_to_terminal:
        await asyncio.to_thread(
            _set_charter_status, engine, charter_id, flip_back_to_terminal
        )


@contextmanager
def _patch_invoke_agent_and_wait(tripwire: dict):
    """Patch the chart tool's ``invoke_agent_and_wait`` and tripwire on call.

    The tripwire is the canonical signal that REUSE engaged (and the legacy
    fresh-spawn path did NOT). The unit-test docs at
    ``tests/test_chart_tools.py:114-218`` use the same approach.
    """
    from unittest.mock import AsyncMock

    tripwire["called"] = 0

    async def _trip(*args, **kwargs):
        tripwire["called"] += 1
        raise AssertionError(
            "invoke_agent_and_wait must NOT be called on the reuse path"
        )

    with patch(
        "daemon.tools.chart_tools.invoke_agent_and_wait",
        AsyncMock(side_effect=_trip),
    ):
        yield tripwire


# ──────────────────────────────────────────────────────────────────────────────
# T1 — real-dispatch revive walk
# ──────────────────────────────────────────────────────────────────────────────


class TestT1RealDispatchReviveWalk:
    """The follow-up ``generate_chart`` call walks the real revive path."""

    async def test_second_call_revives_same_charter_via_real_enqueue(
        self, engine, fresh_registry, caplog
    ):
        """COMPLETED charter → 2nd call reuses SAME id, no second spawn.

        Verifies the load-bearing seams end-to-end:

          * REAL ``_find_reusable_charter`` finds the row via real repo
          * REAL ``_reuse_charter`` calls REAL ``enqueue_message``
          * REAL DB revive: terminal → RUNNING (asserted mid-flight) →
            terminal (after side-task completion)
          * ``invoke_agent_and_wait`` is NEVER awaited (tripwire)
          * Charter children count stays at 1 (no second hierarchy row)
        """
        from daemon.tools.chart_tools import create_chart_tools

        caller_id = "iid-caller-t1-001"
        charter_id = "iid-charter-t1-001"
        rows_before = _count_instance_rows(engine)

        # Caller + completed charter child — REAL DB rows.
        _seed_instance(
            engine, instance_id=caller_id, agent_id="developer", status="running"
        )
        _seed_charter_child(
            engine,
            charter_id=charter_id,
            parent_id=caller_id,
            status="completed",
            last_activity_at=datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc),
        )
        charter_rows_after_seed = _count_instance_rows(engine)
        assert charter_rows_after_seed == rows_before + 2, (
            f"harness row count off: rows_before={rows_before}, "
            f"after_seed={charter_rows_after_seed}"
        )

        mgr = build_harness_manager(engine)
        tripwire: dict = {}

        with caplog.at_level(logging.INFO, logger="daemon.tools.chart_tools"):
            with _patch_invoke_agent_and_wait(tripwire):
                tools = create_chart_tools(mgr, caller_id)

                # Spawn the side task that completes the registry AFTER
                # the enqueue flips the status to RUNNING.
                side_task = asyncio.create_task(
                    _complete_charter_after_enqueue(
                        registry=fresh_registry,
                        charter_id=charter_id,
                        repo=mgr._instance_repository,
                        engine=engine,
                        content="```mermaid\ngraph TD\nA-->B\n```\n\nFirst refine.",
                    )
                )

                result = await tools[0].coroutine(
                    description="Refine the auth flow",
                    diagram_type="sequence",
                )
                # Mid-flight flip pinned: the enqueue flipped
                # terminal→RUNNING at enqueue time (recorded in
                # ``revive_flips`` inside ``_do_revive`` BEFORE the side
                # task re-flips the row terminal). This directly observes
                # the RUNNING moment, not just the final terminal state
                # — what the plan's T1 acceptance requires.
                assert (charter_id, "completed", "running") in mgr.revive_flips, (
                    f"enqueue should flip charter terminal→RUNNING at "
                    f"enqueue time; got {mgr.revive_flips}"
                )
                await side_task

        # (1) Content returned verbatim.
        assert result == "```mermaid\ngraph TD\nA-->B\n```\n\nFirst refine."

        # (2) NO second spawn — invoke_agent_and_wait was never awaited.
        assert tripwire["called"] == 0, (
            f"reuse path invoked spawn! called={tripwire['called']}"
        )

        # (3) enqueue_message was called exactly once, with the discovered
        # charter_id (no second spawn).
        assert mgr.enqueue_message.await_count == 1, (
            f"enqueue_message should be called exactly once, "
            f"got {mgr.enqueue_message.await_count}"
        )
        kwargs = mgr.enqueue_message.call_args.kwargs
        assert kwargs["instance_id"] == charter_id, (
            f"enqueue target must be the discovered charter; got {kwargs}"
        )

        # (4) Charter rows count unchanged (no second spawn row).
        assert _count_instance_rows(engine) == charter_rows_after_seed, (
            "no new instance row should have been inserted"
        )

        # (5) Charter row returned to a terminal status after completion.
        final_status = _read_charter_status(engine, charter_id)
        assert final_status == "completed", (
            f"charter row should be terminal after completion, got {final_status}"
        )

        # (6) Mode log is mode=reuse (the canonical Phase 1 log line).
        records = _mode_records(caplog)
        assert any("mode=reuse" in r.getMessage() for r in records), (
            f"missing mode=reuse log line; got {[r.getMessage() for r in records]}"
        )

    async def test_third_call_revives_revived_charter_unchanged(
        self, engine, fresh_registry
    ):
        """3rd call = revive-of-revived, still the SAME charter id; hierarchy
        rows stay absent between calls (cap headroom never consumed).
        """
        from daemon.tools.chart_tools import create_chart_tools

        caller_id = "iid-caller-t1-002"
        charter_id = "iid-charter-t1-002"

        _seed_instance(
            engine, instance_id=caller_id, agent_id="developer", status="running"
        )
        _seed_charter_child(
            engine,
            charter_id=charter_id,
            parent_id=caller_id,
            status="completed",
            last_activity_at=datetime(2026, 9, 11, 12, 30, tzinfo=timezone.utc),
        )

        mgr = build_harness_manager(engine)
        tripwire: dict = {}

        async def _drive_one_call(label: str) -> str:
            side_task = asyncio.create_task(
                _complete_charter_after_enqueue(
                    registry=fresh_registry,
                    charter_id=charter_id,
                    repo=mgr._instance_repository,
                    engine=engine,
                    content=f"refined-{label}",
                )
            )
            with _patch_invoke_agent_and_wait(tripwire):
                tools = create_chart_tools(mgr, caller_id)
                result = await tools[0].coroutine(
                    description=f"Iter {label}",
                    diagram_type="sequence",
                )
            await side_task
            return result

        r2 = await _drive_one_call("second")
        r3 = await _drive_one_call("third")
        r4 = await _drive_one_call("fourth")

        assert r2 == "refined-second"
        assert r3 == "refined-third"
        assert r4 == "refined-fourth"

        # Every call routed to the SAME charter id.
        assert (
            mgr.enqueue_message.await_count == 3
        ), "three reuse calls → three enqueues"
        for call in mgr.enqueue_message.call_args_list:
            assert call.kwargs["instance_id"] == charter_id, (
                f"every reuse must target the same id; "
                f"got {[c.kwargs['instance_id'] for c in mgr.enqueue_message.call_args_list]}"
            )

        # No hierarchy rows were ever inserted (reuse path doesn't spawn).
        assert _count_hierarchy_rows(engine, parent_id=caller_id) == 0, (
            "reuse must NOT insert hierarchy rows; "
            "spawn-cap headroom stays untouched"
        )

        # No new instance rows.
        total = _count_instance_rows(engine)
        assert total == 2, (
            f"expected caller + charter only, got {total} rows"
        )

        assert tripwire["called"] == 0

    async def test_discovery_determinism_latest_last_activity_wins(
        self, engine, fresh_registry
    ):
        """Two charter children with staggered last_activity_at → walk
        reuses the LATEST. Echoes the Phase 1 T8.8 unit pin on real rows.
        """
        from daemon.tools.chart_tools import create_chart_tools

        caller_id = "iid-caller-t1-003"
        old_charter = "iid-charter-old"
        new_charter = "iid-charter-new"

        _seed_instance(
            engine, instance_id=caller_id, agent_id="developer", status="running"
        )
        # Older charter — last_activity_at earlier
        _seed_charter_child(
            engine,
            charter_id=old_charter,
            parent_id=caller_id,
            status="completed",
            last_activity_at=datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc),
            created_at=datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc),
        )
        # Newer charter — last_activity_at later
        _seed_charter_child(
            engine,
            charter_id=new_charter,
            parent_id=caller_id,
            status="completed",
            last_activity_at=datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc),
            created_at=datetime(2026, 9, 11, 13, 0, tzinfo=timezone.utc),
        )

        mgr = build_harness_manager(engine)
        tripwire: dict = {}

        side_task = asyncio.create_task(
            _complete_charter_after_enqueue(
                registry=fresh_registry,
                charter_id=new_charter,
                repo=mgr._instance_repository,
                engine=engine,
                content="newest-wins",
            )
        )

        with _patch_invoke_agent_and_wait(tripwire):
            tools = create_chart_tools(mgr, caller_id)
            result = await tools[0].coroutine(
                description="refine",
                diagram_type="flowchart",
            )
        await side_task

        assert result == "newest-wins"
        assert mgr.enqueue_message.call_args.kwargs["instance_id"] == new_charter

        # The older charter was left untouched (orphaned silently, per
        # plan-overview R5: same last-write-wins semantics).
        assert _read_charter_status(engine, old_charter) == "completed"

    async def test_spawn_vs_revive_latency_delta(self, engine, fresh_registry):
        """Record a rough spawn-vs-revive latency delta number for the
        plan's T1 step 7 — open question, immaterial to the mechanism
        choice, but the architect wants a number.

        Mirrors the V5 walk's real-routing convention (real components,
        no LLM, no graph assembly). The reuse path's wall-clock cost is
        dominated by the side-task scheduler delay + DB commit; the
        "spawn" path here is a synthetic baseline measured by timing a
        stubbed ``invoke_agent_and_wait`` call (the closest stand-in for
        the production fresh-spawn cost without bringing up a worker pool
        and graph).
        """
        from daemon.tools.chart_tools import create_chart_tools

        caller_id = "iid-caller-t1-latency"
        charter_id = "iid-charter-t1-latency"

        _seed_instance(
            engine, instance_id=caller_id, agent_id="developer", status="running"
        )
        _seed_charter_child(
            engine,
            charter_id=charter_id,
            parent_id=caller_id,
            status="completed",
            last_activity_at=datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc),
        )

        # ── Revise timing — REUSE path.
        mgr = build_harness_manager(engine)
        # Warm the asyncio loop so the first measurement isn't a cold-start.
        await asyncio.sleep(0)

        reuse_samples: list[float] = []
        spawn_samples: list[float] = []

        # 5 trials each — small N is fine for an "architect wants a number"
        # figure, not a perf benchmark.
        for trial in range(5):
            fresh_registry.unregister(charter_id)

            side_task = asyncio.create_task(
                _complete_charter_after_enqueue(
                    registry=fresh_registry,
                    charter_id=charter_id,
                    repo=mgr._instance_repository,
                    engine=engine,
                    content=f"trial-{trial}",
                    delay_seconds=0.0,  # no delay — pure wait_for path
                )
            )

            tripwire: dict = {}
            t0 = asyncio.get_event_loop().time()
            with _patch_invoke_agent_and_wait(tripwire):
                tools = create_chart_tools(mgr, caller_id)
                await tools[0].coroutine(description=f"reuse-{trial}")
            t1 = asyncio.get_event_loop().time()
            await side_task
            reuse_samples.append((t1 - t0) * 1000.0)  # ms

            # Same caller, fresh=True — stub ``invoke_agent_and_wait`` with
            # the cheapest possible successful return (a 0.01s sleep
            # matches the worker's selection overhead; the production
            # path adds graph assembly + worker-pool dispatch, but that's
            # OUT of scope for this latency sketch).
            async def _spawn_stub(*_a, **_kw):
                await asyncio.sleep(0.01)
                return ("fresh-output", f"new-id-{trial}")

            with patch(
                "daemon.tools.chart_tools.invoke_agent_and_wait",
                AsyncMock(side_effect=_spawn_stub),
            ):
                t2 = asyncio.get_event_loop().time()
                tools = create_chart_tools(mgr, caller_id)
                await tools[0].coroutine(description=f"spawn-{trial}", fresh=True)
                t3 = asyncio.get_event_loop().time()
            spawn_samples.append((t3 - t2) * 1000.0)

        reuse_median = sorted(reuse_samples)[len(reuse_samples) // 2]
        spawn_median = sorted(spawn_samples)[len(spawn_samples) // 2]
        delta_ms = spawn_median - reuse_median

        # Stash on the test class so the final report can read it.
        TestT1RealDispatchReviveWalk.latency_delta_ms = delta_ms  # type: ignore[attr-defined]
        TestT1RealDispatchReviveWalk.latency_reuse_median_ms = reuse_median  # type: ignore[attr-defined]
        TestT1RealDispatchReviveWalk.latency_spawn_median_ms = spawn_median  # type: ignore[attr-defined]

        # Sanity bounds: reuse must complete in the tens-of-ms range
        # (no LLM, no graph); spawn stub is at least 10ms (one event-loop
        # tick of sleep).
        assert reuse_median < 100.0, (
            f"reuse latency unexpectedly high: {reuse_median}ms"
        )
        assert spawn_median > 0.0, (
            f"spawn stub latency should be > 0: {spawn_median}ms"
        )


# ──────────────────────────────────────────────────────────────────────────────
# T2 — budget & lifecycle non-interaction pins
# ──────────────────────────────────────────────────────────────────────────────


class TestT2BudgetLifecycleNonInteraction:
    """Pin: ReviveGuard counter stays 0; spawn-cap headroom not consumed."""

    async def test_reviveguard_counter_stays_zero_across_status_cycles(
        self, engine, fresh_registry
    ):
        """COMPLETED → ERROR → TERMINATED reuse cycles: agent-tool
        ``get_agent_tool_revive_count`` stays 0 throughout.
        """
        from daemon.tools.chart_tools import create_chart_tools

        caller_id = "iid-caller-t2-001"
        charter_id = "iid-charter-t2-001"

        _seed_instance(
            engine, instance_id=caller_id, agent_id="developer", status="running"
        )
        _seed_charter_child(
            engine,
            charter_id=charter_id,
            parent_id=caller_id,
            status="completed",
            last_activity_at=datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc),
        )

        mgr = build_harness_manager(engine)
        tripwire: dict = {}

        # (1) COMPLETED cycle.
        with _patch_invoke_agent_and_wait(tripwire):
            tools = create_chart_tools(mgr, caller_id)
            side_task = asyncio.create_task(
                _complete_charter_after_enqueue(
                    registry=fresh_registry,
                    charter_id=charter_id,
                    repo=mgr._instance_repository,
                    engine=engine,
                    content="ok-completed",
                    flip_back_to_terminal="completed",
                )
            )
            await tools[0].coroutine(description="c1")
            await side_task

        assert mgr.get_agent_tool_revive_count(charter_id) == 0, (
            "COMPLETED-cycle reuse MUST NOT bump the agent-tool ReviveGuard"
        )

        # (2) ERROR cycle — flip to ERROR, reuse, flip back to ERROR.
        _set_charter_status(engine, charter_id, "error")
        with _patch_invoke_agent_and_wait(tripwire):
            tools = create_chart_tools(mgr, caller_id)
            side_task = asyncio.create_task(
                _complete_charter_after_enqueue(
                    registry=fresh_registry,
                    charter_id=charter_id,
                    repo=mgr._instance_repository,
                    engine=engine,
                    content="ok-after-error",
                    is_error=False,
                    flip_back_to_terminal="error",
                )
            )
            await tools[0].coroutine(description="c2")
            await side_task

        assert mgr.get_agent_tool_revive_count(charter_id) == 0, (
            "ERROR-cycle reuse (FIRST attempt) MUST NOT bump the ReviveGuard"
        )

        # (3) TERMINATED cycle — operator-killed orphan, free revive.
        _set_charter_status(engine, charter_id, "terminated")
        with _patch_invoke_agent_and_wait(tripwire):
            tools = create_chart_tools(mgr, caller_id)
            side_task = asyncio.create_task(
                _complete_charter_after_enqueue(
                    registry=fresh_registry,
                    charter_id=charter_id,
                    repo=mgr._instance_repository,
                    engine=engine,
                    content="ok-after-terminated",
                    flip_back_to_terminal="terminated",
                )
            )
            await tools[0].coroutine(description="c3")
            await side_task

        assert mgr.get_agent_tool_revive_count(charter_id) == 0, (
            "TERMINATED-cycle reuse (operator-killed orphan) MUST stay free"
        )

        # (4) Subsequent COMPLETED revives N times — never touch the counter.
        for i in range(3):
            _set_charter_status(engine, charter_id, "completed")
            with _patch_invoke_agent_and_wait(tripwire):
                tools = create_chart_tools(mgr, caller_id)
                side_task = asyncio.create_task(
                    _complete_charter_after_enqueue(
                        registry=fresh_registry,
                        charter_id=charter_id,
                        repo=mgr._instance_repository,
                        engine=engine,
                        content=f"ok-iter-{i}",
                        flip_back_to_terminal="completed",
                    )
                )
                await tools[0].coroutine(description=f"extra-{i}")
                await side_task

        assert mgr.get_agent_tool_revive_count(charter_id) == 0, (
            "N successive COMPLETED revives must keep the counter at 0"
        )

        # Tripwire never fired — REUSE engaged every time.
        assert tripwire["called"] == 0

        # Non-vacuity proof: the harness's REAL dict-backed counter
        # mechanism stayed empty through the whole COMPLETED → ERROR →
        # TERMINATED cycle. The previous MagicMock was hard-wired to
        # return 0 and could not detect a regression; with the dict
        # backing, IF production chart code ever started calling
        # ``manager.note_agent_tool_revive``, this dict would gain
        # entries (consuming for ERROR/FAILED, non-consuming for
        # COMPLETED/TERMINATED per the production mirror) and this
        # assertion would FAIL — that's what makes the ``== 0`` pins
        # above non-vacuous.
        assert mgr._agent_tool_revive_counts == {}, (
            "agent-tool ReviveGuard counter dict MUST stay empty across "
            "the whole cycle; non-vacuity proof for the ==0 pins above"
        )

    async def test_reuse_does_not_consume_spawn_cap_headroom(
        self, engine, fresh_registry
    ):
        """With ``limits.max_children_per_instance=1`` and the caller at the
        cap (1 ACTIVE hierarchy row), reuse still works (no spawn needed).
        The completed charter is invisible to ``count_children`` (hierarchy
        rows deleted at child completion) — so the cap headroom calculation
        never even enters the reuse path.
        """
        from daemon.tools.chart_tools import create_chart_tools

        caller_id = "iid-caller-t2-002"
        charter_id = "iid-charter-t2-002"
        sibling_id = "iid-sibling-active"

        # Cap = 1 — caller AT cap with 1 ACTIVE sibling (hierarchy row).
        mgr = build_harness_manager(engine, max_children_per_instance=1)
        tripwire: dict = {}

        _seed_instance(
            engine, instance_id=caller_id, agent_id="developer", status="running"
        )
        # 1 ACTIVE sibling → hierarchy row → caller at cap.
        _seed_instance(
            engine,
            instance_id=sibling_id,
            agent_id="worker",
            parent_id=caller_id,
            status="running",
            metadata={"invoked_as_tool": True},
        )
        # Completed charter child — NO hierarchy row (would have been
        # deleted by the production cleanup-on-completion path). Even with
        # the row present, reuse does NOT consult the cap.
        _seed_charter_child(
            engine,
            charter_id=charter_id,
            parent_id=caller_id,
            status="completed",
            last_activity_at=datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc),
        )

        # Verify the cap arithmetic the spawn would consult:
        assert (
            mgr._instance_repository.count_children(caller_id) == 1
        ), "cap = 1 + 1 active sibling = at cap"
        # Reuse must still engage.
        side_task = asyncio.create_task(
            _complete_charter_after_enqueue(
                registry=fresh_registry,
                charter_id=charter_id,
                repo=mgr._instance_repository,
                engine=engine,
                content="ok-at-cap",
            )
        )
        with _patch_invoke_agent_and_wait(tripwire):
            tools = create_chart_tools(mgr, caller_id)
            result = await tools[0].coroutine(description="at-cap")
        await side_task

        assert result == "ok-at-cap"
        assert tripwire["called"] == 0
        assert mgr.enqueue_message.call_args.kwargs["instance_id"] == charter_id
        # Sanity: hierarchy rows unchanged.
        assert _count_hierarchy_rows(engine, parent_id=caller_id) == 1


# ──────────────────────────────────────────────────────────────────────────────
# T3 — ERROR/FAILED policy end-to-end
# ──────────────────────────────────────────────────────────────────────────────


class TestT3ErrorPolicyEnd2End:
    """ERROR/FAILED revive policy: 1st call revives, 2nd call respawns fresh."""

    async def test_error_charter_revives_then_respawns_on_second_failure(
        self, engine, fresh_registry, caplog
    ):
        """ERROR charter:

          * 1st call revives (counter 1, mode=reuse)
          * 2nd turn fails again
          * 2nd call spawns FRESH (new id, mode=reuse-respawn-after-failure)
          * old ERROR charter left untouched (no terminate)
        """
        from daemon.tools.chart_tools import create_chart_tools

        caller_id = "iid-caller-t3-001"
        charter_id = "iid-charter-t3-001"
        fresh_charter_id = "iid-charter-t3-fresh"

        _seed_instance(
            engine, instance_id=caller_id, agent_id="developer", status="running"
        )
        _seed_charter_child(
            engine,
            charter_id=charter_id,
            parent_id=caller_id,
            status="error",
            last_activity_at=datetime(2026, 9, 11, 11, 0, tzinfo=timezone.utc),
        )

        mgr = build_harness_manager(engine)

        # ───── 1st call: revives the ERROR charter ─────────────────────────
        side_task = asyncio.create_task(
            _complete_charter_after_enqueue(
                registry=fresh_registry,
                charter_id=charter_id,
                repo=mgr._instance_repository,
                engine=engine,
                content="recovered",
                is_error=False,
                flip_back_to_terminal="error",  # fail again → status=error
            )
        )

        with caplog.at_level(logging.INFO, logger="daemon.tools.chart_tools"):
            tools = create_chart_tools(mgr, caller_id)
            result1 = await tools[0].coroutine(description="retry1")
        await side_task

        assert result1 == "recovered"
        assert mgr.enqueue_message.await_count == 1
        assert (
            mgr.enqueue_message.call_args.kwargs["instance_id"] == charter_id
        )
        # Local counter (chart-path-only) bumped by exactly 1.
        assert chart_tools_module._reuse_revive_attempts.get(charter_id) == 1
        # Agent-tool ReviveGuard stays 0.
        assert mgr.get_agent_tool_revive_count(charter_id) == 0

        # ───── 2nd call: discovery finds ERROR charter with counter >= 1 →
        # respawn log fires, chart tool falls through to invoke_agent_and_wait
        # (the FRESH path). ``invoke_agent_and_wait`` is stubbed to return
        # canned content + the fresh charter id (so the next call would
        # discover the fresh charter — verified by a tiny follow-up call).
        fresh_registry.unregister(charter_id)

        with patch(
            "daemon.tools.chart_tools.invoke_agent_and_wait",
            AsyncMock(return_value=("fresh-content", fresh_charter_id)),
        ):
            with caplog.at_level(logging.INFO, logger="daemon.tools.chart_tools"):
                tools = create_chart_tools(mgr, caller_id)
                result2 = await tools[0].coroutine(description="retry2")

        assert result2 == "fresh-content"
        # 2nd call did NOT reuse — no second enqueue_message call.
        assert mgr.enqueue_message.await_count == 1, (
            "the respawn branch must NOT call enqueue_message "
            "(it goes through invoke_agent_and_wait instead)"
        )
        # mode=reuse-respawn-after-failure log fired on the 2nd call.
        assert any(
            "mode=reuse-respawn-after-failure" in r.getMessage()
            for r in _mode_records(caplog)
        ), f"expected respawn log; got {[r.getMessage() for r in _mode_records(caplog)]}"

        # Old charter left UNTOUCHED (no terminate, M8).
        assert _read_charter_status(engine, charter_id) == "error", (
            "old ERROR charter MUST be left untouched (M8 no-terminate rule)"
        )

        # Following the plan: "next discovery finds the new charter
        # (latest last_activity_at)". The 2nd call invoked
        # ``invoke_agent_and_wait`` which would normally spawn a new
        # charter row; we simulate that by seeding the fresh charter with
        # a later last_activity_at than the original (mirrors what the
        # spawn path would write). A 3rd discovery call then routes
        # through the FRESH charter — pinning the freshness semantics.
        # The seeded last_activity_at must be later than the ERROR
        # charter's bumped-to-now stamp (the revive bumped it).
        _seed_charter_child(
            engine,
            charter_id=fresh_charter_id,
            parent_id=caller_id,
            status="completed",
            last_activity_at=datetime(2099, 1, 1, 0, 0, tzinfo=timezone.utc),
        )

        # Sanity: the fresh charter is visible to the next discovery.
        from daemon.tools.chart_tools import _find_reusable_charter

        reusable = _find_reusable_charter(mgr, caller_id)
        assert reusable is not None and reusable.instance_id == fresh_charter_id, (
            f"fresh charter must be discoverable; got {reusable.instance_id if reusable else None}"
        )

        side_task = asyncio.create_task(
            _complete_charter_after_enqueue(
                registry=fresh_registry,
                charter_id=fresh_charter_id,
                repo=mgr._instance_repository,
                engine=engine,
                content="next-refine",
            )
        )
        tripwire: dict = {}
        with _patch_invoke_agent_and_wait(tripwire):
            tools = create_chart_tools(mgr, caller_id)
            result3 = await tools[0].coroutine(description="third")
        await side_task

        assert result3 == "next-refine"
        assert mgr.enqueue_message.call_args.kwargs["instance_id"] == fresh_charter_id, (
            "the third call must discover the FRESH charter (latest "
            "last_activity_at), not the original ERROR charter"
        )
        assert tripwire["called"] == 0

    async def test_completed_charter_never_bumps_local_counter(
        self, engine, fresh_registry
    ):
        """CONTROL: N successive COMPLETED revives never touch the local
        counter — only ERROR/FAILED consumes.
        """
        from daemon.tools.chart_tools import create_chart_tools

        caller_id = "iid-caller-t3-002"
        charter_id = "iid-charter-t3-002"

        _seed_instance(
            engine, instance_id=caller_id, agent_id="developer", status="running"
        )
        _seed_charter_child(
            engine,
            charter_id=charter_id,
            parent_id=caller_id,
            status="completed",
            last_activity_at=datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc),
        )

        mgr = build_harness_manager(engine)
        tripwire: dict = {}

        for i in range(5):
            fresh_registry.unregister(charter_id)
            side_task = asyncio.create_task(
                _complete_charter_after_enqueue(
                    registry=fresh_registry,
                    charter_id=charter_id,
                    repo=mgr._instance_repository,
                    engine=engine,
                    content=f"ok-{i}",
                )
            )
            with _patch_invoke_agent_and_wait(tripwire):
                tools = create_chart_tools(mgr, caller_id)
                await tools[0].coroutine(description=f"iter-{i}")
            await side_task

        assert chart_tools_module._reuse_revive_attempts.get(charter_id, 0) == 0, (
            "COMPLETED revives MUST NOT touch the local revive counter"
        )
        assert tripwire["called"] == 0


# ──────────────────────────────────────────────────────────────────────────────
# T5 — fan-out scoping acceptance
# ──────────────────────────────────────────────────────────────────────────────


class TestT5FanOutScoping:
    """Sibling callers → distinct charters; same caller → same charter id."""

    async def test_sibling_callers_get_distinct_charters(
        self, engine, fresh_registry
    ):
        """Caller A's first call spawns charter A (or reuses); caller B's
        first call discovers charter B — distinct ids, no cross-talk.
        """
        from daemon.tools.chart_tools import create_chart_tools

        caller_a = "iid-caller-A"
        caller_b = "iid-caller-B"
        charter_a = "iid-charter-A"
        charter_b = "iid-charter-B"

        _seed_instance(
            engine, instance_id=caller_a, agent_id="developer", status="running"
        )
        _seed_instance(
            engine, instance_id=caller_b, agent_id="developer", status="running"
        )
        _seed_charter_child(
            engine,
            charter_id=charter_a,
            parent_id=caller_a,
            status="completed",
            last_activity_at=datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc),
        )
        _seed_charter_child(
            engine,
            charter_id=charter_b,
            parent_id=caller_b,
            status="completed",
            last_activity_at=datetime(2026, 9, 11, 12, 5, tzinfo=timezone.utc),
        )

        mgr_a = build_harness_manager(engine)
        mgr_b = build_harness_manager(engine)

        # Anchor ``side_a`` to caller A's start; ``side_b`` is created
        # only AFTER ``result_a`` returns so its 0.01s sleep reliably
        # outlives caller B's register (W1 fix: unregister-before-register
        # drains any stale buffered completion that arrived before
        # register, so the side task must fire AFTER register for the
        # completion to be captured by wait_for).
        side_a = asyncio.create_task(
            _complete_charter_after_enqueue(
                registry=fresh_registry,
                charter_id=charter_a,
                repo=mgr_a._instance_repository,
                engine=engine,
                content="chart-A",
            )
        )

        # Each tool closes over its own ``current_instance_id``; that's how
        # per-caller scoping is enforced (chart_tools.py:_find_reusable_charter
        # uses ``current_instance_id`` as the get_children arg).
        tools_a = create_chart_tools(mgr_a, caller_a)
        tools_b = create_chart_tools(mgr_b, caller_b)
        result_a = await tools_a[0].coroutine(description="a")
        await side_a

        side_b = asyncio.create_task(
            _complete_charter_after_enqueue(
                registry=fresh_registry,
                charter_id=charter_b,
                repo=mgr_b._instance_repository,
                engine=engine,
                content="chart-B",
            )
        )
        result_b = await tools_b[0].coroutine(description="b")
        await side_b

        assert result_a == "chart-A"
        assert result_b == "chart-B"

        # A's enqueue → charter A only; B's enqueue → charter B only.
        # ZERO cross-talk.
        assert mgr_a.enqueue_message.await_count == 1
        assert mgr_b.enqueue_message.await_count == 1
        assert mgr_a.enqueue_message.call_args.kwargs["instance_id"] == charter_a
        assert mgr_b.enqueue_message.call_args.kwargs["instance_id"] == charter_b

    async def test_same_caller_charter_continuity_across_caller_revival(
        self, engine, fresh_registry
    ):
        """Same caller's 2nd and 3rd calls hit the SAME charter id, even
        across a caller-level terminal→revive cycle (the mapping key is
        ``current_instance_id`` which is the caller's UUID — survives
        caller revival).
        """
        from daemon.tools.chart_tools import create_chart_tools

        caller_id = "iid-caller-T5"
        charter_id = "iid-charter-T5"

        _seed_instance(
            engine, instance_id=caller_id, agent_id="developer", status="running"
        )
        _seed_charter_child(
            engine,
            charter_id=charter_id,
            parent_id=caller_id,
            status="completed",
            last_activity_at=datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc),
        )

        mgr = build_harness_manager(engine)
        tripwire: dict = {}

        async def _drive(desc: str) -> str:
            fresh_registry.unregister(charter_id)
            side = asyncio.create_task(
                _complete_charter_after_enqueue(
                    registry=fresh_registry,
                    charter_id=charter_id,
                    repo=mgr._instance_repository,
                    engine=engine,
                    content=f"refined-{desc}",
                )
            )
            with _patch_invoke_agent_and_wait(tripwire):
                tools = create_chart_tools(mgr, caller_id)
                result = await tools[0].coroutine(description=desc)
            await side
            return result

        # 1st reuse — discovers charter_id.
        r1 = await _drive("first")
        # Caller-level terminal cycle: mark caller COMPLETED → revive via
        # in-memory state (no real enqueue needed at the caller row; the
        # charter reuse stays keyed on the caller's instance_id which
        # is unchanged).
        _set_charter_status(engine, caller_id, "completed")
        r2 = await _drive("second-after-caller-terminal")
        # Caller revival back to running.
        _set_charter_status(engine, caller_id, "running")
        r3 = await _drive("third-after-revival")

        assert r1 == "refined-first"
        assert r2 == "refined-second-after-caller-terminal"
        assert r3 == "refined-third-after-revival"

        assert mgr.enqueue_message.await_count == 3
        for call in mgr.enqueue_message.call_args_list:
            assert call.kwargs["instance_id"] == charter_id, (
                f"all 3 calls routed to SAME charter id (caller key stable); "
                f"got {[c.kwargs['instance_id'] for c in mgr.enqueue_message.call_args_list]}"
            )


# ──────────────────────────────────────────────────────────────────────────────
# T6 — facade-forwarding verification (always run)
# ──────────────────────────────────────────────────────────────────────────────


class TestT6FacadeForwarding:
    """The Phase 1 diff did NOT add any new kwargs to ``enqueue_message`` /
    ``InstanceMessagingService`` / repository methods. Verifies the design
    constraint that the reuse path passes only existing kwargs.
    """

    PHASE1_COMMIT = "25c56265"

    def test_no_new_kwargs_on_enqueue_message_in_phase1_diff(self):
        """The Phase 1 diff added NO new kwargs to ``enqueue_message``,
        ``InstanceMessagingService.enqueue_message``, or any repository
        method. Verifies M11 — facade-forwarding discipline satisfied
        vacuously.
        """
        # Diff the Phase 1 commit. We use ``git show --stat`` for the
        # summary and ``git show -G`` for a content grep on the diff hunks.
        try:
            show_stat = subprocess.run(
                [
                    "git",
                    "show",
                    self.PHASE1_COMMIT,
                    "--stat",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            pytest.skip(
                f"git show {self.PHASE1_COMMIT} failed: {exc.stderr.strip()}"
            )

        # The diff is constrained to chart_tools.py, tests/test_chart_tools.py,
        # and daemon/constants.py. Per the Phase 1 commit stat:
        #   daemon/constants.py         |   2 +
        #   daemon/tools/chart_tools.py | 327 +++++++++++++++++++++
        #   tests/test_chart_tools.py   | 689 +++++++++++++++++++++++++++++++++++++++++-
        # (None of these touch ``manager``, ``InstanceMessagingService``,
        # or any repository method's signature.)
        stat_text = show_stat.stdout
        assert "daemon/tools/chart_tools.py" in stat_text
        assert "tests/test_chart_tools.py" in stat_text
        assert "daemon/constants.py" in stat_text

        # Now grep the full diff for any added kwargs on the load-bearing
        # surfaces. We expect ZERO matches: the reuse path passes only
        # existing kwargs to ``manager.enqueue_message`` (``instance_id``,
        # ``message``, ``source``, ``metadata`` — all pre-existing).
        diff_text = subprocess.run(
            ["git", "show", self.PHASE1_COMMIT],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

        # ``enqueue_message`` surface: signature change would show up as
        # an added parameter in ``chart_tools.py``. We use a defensive
        # ``git show -G`` to grep the diff hunks for new parameter lines.
        show_grep = subprocess.run(
            [
                "git",
                "show",
                "-G",
                r"^\s*(instance_id|priority|images|metadata|is_deferred|is_background|work_id|work_id_required)\s*:",
                self.PHASE1_COMMIT,
                "--",
                "daemon/tools/chart_tools.py",
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout

        # Filter out pre-existing kwargs. The reuse code calls:
        #   await manager.enqueue_message(
        #       instance_id=charter_id,
        #       message=message,
        #       source=f"internal_chart_reuse:{caller_id}",
        #       metadata={"chart_reuse": True},
        #   )
        # — all four are existing kwargs. The defensive grep above should
        # surface those lines, and we assert NONE of the kwargs listed
        # in the chart_tools.py hunks are NEW (i.e., added in the diff
        # but absent from the prior signature).
        #
        # Facade-forwarding holds: the assertion below proves
        # manager.enqueue_message's signature was untouched in Phase 1
        # (``daemon/manager.py`` is absent from the diff stat), so the
        # reuse path passes ONLY pre-existing kwargs through the facade.
        assert "daemon/manager.py" not in stat_text, (
            "Phase 1 diff MUST NOT touch daemon/manager.py — facade-forwarding "
            "discipline holds only if the manager facade is unchanged"
        )

        # The Phase 1 chart_tools.py introduced no ``enqueue_message``
        # call with kwargs beyond the documented four. Cross-check the
        # chart_tools diff for any new keyword on the manager.enqueue_message
        # call site.
        # Extract the relevant enqueue_message call from the diff.
        call_pattern = re.compile(
            r"manager\.enqueue_message\((?P<args>[^)]*)\)",
            re.DOTALL,
        )
        for match in call_pattern.finditer(diff_text):
            args = match.group("args")
            # Strip whitespace + the leading / trailing comment lines for
            # a clean kwarg list.
            arg_lines = [
                ln.strip().rstrip(",")
                for ln in args.splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            for line in arg_lines:
                m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
                if m:
                    kwarg = m.group(1)
                    assert kwarg in {
                        "instance_id",
                        "message",
                        "source",
                        "metadata",
                    }, (
                        f"unexpected kwarg on manager.enqueue_message call "
                        f"in Phase 1 diff: {kwarg!r}"
                    )

        # Smoke check on messaging_types: AsyncMessageResult was NOT
        # touched by the diff either (no field additions).
        assert "daemon/services/messaging_types.py" not in stat_text
        assert "daemon/services/instance_messaging.py" not in stat_text
        assert "daemon/services/instance_lifecycle.py" not in stat_text
        assert "daemon/repositories/instance/repository.py" not in stat_text

        # All evidence captured — record the diff hash on the test
        # instance for grep-evidence reporting.
        TestT6FacadeForwarding.last_grep_evidence = (  # type: ignore[attr-defined]
            f"git show {self.PHASE1_COMMIT} --stat | grep '^ ' "
            f"=> only daemon/constants.py + daemon/tools/chart_tools.py + "
            f"tests/test_chart_tools.py changed; "
            f"chart_tools.py enqueue_message call uses only "
            f"existing kwargs (instance_id, message, source, metadata)"
        )


# ──────────────────────────────────────────────────────────────────────────────
# T7 — compaction canary: OPTIONAL, skipped per the plan ("skip without
# ceremony if the fixture budget is tight"). Note only — not a gate.
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.skip(reason="T7 OPTIONAL — compaction canary skipped per phase3-plan.md")
class TestT7CompactionCanary:
    """Compaction canary — would seed a refine history large enough to
    cross the L1 pre-dispatch threshold (0.80 × DEFAULT_CONTEXT_LIMIT =
    700k) and assert a mid-loop compaction event is observable. Realistic
    refine histories (10–150 KB) sit orders of magnitude below; the canary
    is insurance, not acceptance. Skipped per the plan.
    """
