"""E1 stranger-spawn refusal RED pins — commission ``scheduler-deferred-alarm-delivery``.

Two pins, both RED BY DESIGN on the pre-fix tree (plan: FROZEN
``.agents/shared/planning/scheduler-deferred-alarm-delivery/`` — decisions.md
DEC-5/DEC-9-CRIT-1/DEC-21, plan-overview.md §(e) rows 5–6):

1. ``TestE1RefusesStrangerSpawn::test_bound_caller_confirmed_gone_refuses_instead_of_spawning``
   — fire-time: the bound caller is CONFIRMED gone (absent from memory AND
   DB, successful query → ``False``) ⇒ the mapper raises the dedicated typed
   refusal; the delivery ends as ``refused_permanent`` (consume VISIBLY:
   one-shot consumed, ``failed`` execution row carrying the refusal reason)
   and the stranger-spawn door (``mapper.py`` stale-mapping delete + the
   no-mapping fall-through → ``spawn_instance_with_mcp``) is NEVER taken.
   Includes the ADR-11 contrast leg: an UNCONFIRMED absence (the
   ``_mapping_instance_exists`` DB-error path, ``except → return True``)
   must NOT refuse — it returns the existing mapping and spawns nothing.

2. ``TestE1RefusesStrangerSpawn::test_binding_write_failure_refuses_to_arm_and_leaves_the_schedule_unarmed``
   — arm-time (DEC-21 refuse-to-arm): a binding-write failure ⇒ REFUSE to
   arm with a typed error naming the binding failure. The named assertion
   "a refused arm cannot fire" (restated per Blocking 2): the adapter is
   EVICTED from the registry and STOPPED (row flags alone cannot stop the
   loop — ``enabled`` is read only at boot, ``registry.py:291``; the loop
   never re-reads it, ``scheduler.py:519-520``), the schedule row is left
   ``status='stopped'`` + ``enabled=False`` (F1 — NOT deleted), no mapping
   exists, a forced fire attempt through the sanctioned seam yields no
   delivery, and no spawn occurs.

LINEAGE — harness patterns copied verbatim (with adaptations noted inline)
from the substrate seed ``tests/unit/test_scheduler_deferred_mechanism_probe.py``
(unmodified): the file-backed-SQLite engine fixture (seed ``probe_engine``
fallback shape: file DB + ``NullPool`` + ``check_same_thread=False``, never
``StaticPool``/``:memory:``), the row-seeding helpers, the
``_build_scheduler_adapter`` real-adapter-over-real-repos wiring, and the
``_production_on_complete`` one-shot-consumption replica. The row-writing
execution callback is a replica of ``daemon/sources/registry.py``
``_safe_sync_callback`` (:472-496) minus the thread-pool hop (the pin runs
it inline on the loop thread).

FENCE: hermetic SQLite file under ``tmp_path`` only. No PostgreSQL, no
daemon boot, no port binding, no live install, never ``ensemble_prod``.
No daemon source file is imported-and-mutated; the only state this file
touches is its own throwaway DB plus module attributes it restores
(``scheduling_service._deps``) or monkeypatches (the three
``get_registry`` binding sites, function-scoped).

NOTHING outside this file is created or modified. No git operations.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, select

import daemon.repositories.instance.models  # noqa: F401  (register tables)
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.message_queue.models  # noqa: F401
import daemon.repositories.project.models  # noqa: F401
import daemon.repositories.source.models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401
import daemon.services.scheduling_service as scheduling_service
import daemon.sources.adapters.scheduler as scheduler_module
import daemon.sources.mapper as mapper_module
from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository
from daemon.repositories.job_queue.models import JobItem
from daemon.repositories.message_queue.models import MessageQueue
from daemon.repositories.project.models import Project, ProjectStatus
from daemon.repositories.source.models import (
    InstanceMapping,
    ScheduleExecution,
    SourceConfig as SourceConfigRow,
)
from daemon.repositories.source.repository import SQLModelSourceRepository
from daemon.repositories.task.models import Task
from daemon.sources.adapters.scheduler import SchedulerAdapter
from daemon.sources.base import SourceConfig as AdapterSourceConfig
from daemon.sources.base import SourceStatus
from daemon.sources.mapper import InstanceMapper
from daemon.sources.registry import SourceRegistry
from daemon.tools.self_alarm import _arm_self_alarm, effective_label_for

PIN_TAG = "e1_refusal_pin"

#: The canonical name of the dedicated typed refusal (plan decisions.md,
#: DEC-9 CRIT-1 Part 2/3 sketch: ``except BoundCallerRefusal as refusal:``).
#: It does not exist on the pre-fix tree — the pins' first assertion turns
#: that absence into the expected clean RED.
_REFUSAL_TYPE_NAME = "BoundCallerRefusal"

#: Modules the implementation may home the typed refusal in (all three are
#: in the plan's 8-MODIFIED inventory; mapper is the primary raise site).
_REFUSAL_TYPE_HOMES = (
    "daemon.sources.mapper",
    "daemon.tools.self_alarm",
    "daemon.services.scheduling_service",
)


# ---------------------------------------------------------------------------
# RED-for-the-right-reason guard
# ---------------------------------------------------------------------------


def _find_refusal_type() -> tuple[type | None, str | None]:
    """Resolve the dedicated typed refusal type, or ``(None, None)`` at base.

    The plan fixes the NAME (``BoundCallerRefusal``) but not the module, so
    the search covers the three plan-named homes. Import errors inside a
    home are fatal-by-nature (module import); all three import cleanly on
    the pre-fix tree.
    """
    import daemon.services.scheduling_service as _ss
    import daemon.sources.mapper as _mapper
    import daemon.tools.self_alarm as _sa

    for modname, mod in (
        ("daemon.sources.mapper", _mapper),
        ("daemon.tools.self_alarm", _sa),
        ("daemon.services.scheduling_service", _ss),
    ):
        candidate = getattr(mod, _REFUSAL_TYPE_NAME, None)
        if candidate is not None:
            return candidate, modname
    return None, None


# ---------------------------------------------------------------------------
# Hermetic engine (seed lineage: probe_engine SQLite fallback shape)
# ---------------------------------------------------------------------------


@pytest.fixture
def pin_engine(tmp_path) -> Engine:
    """A REAL file-backed SQLite engine under ``tmp_path`` (hermetic).

    Seed lineage: ``test_scheduler_deferred_mechanism_probe.py::probe_engine``
    fallback branch — file DB + ``NullPool`` + ``check_same_thread=False``.
    This pin is SQLite-ONLY by mandate: no PostgreSQL, no throwaway schema,
    no daemon boot.
    """
    eng = create_engine(
        f"sqlite:///{tmp_path}/{PIN_TAG}.db",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


# ---------------------------------------------------------------------------
# Row seeding + row readers (seed lineage)
# ---------------------------------------------------------------------------


def _seed_project(eng: Engine, project_id: str) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(eng) as session:
        session.add(
            Project(
                project_id=project_id,
                name=f"{PIN_TAG}-proj-{project_id[:8]}",
                project_type="system",
                status=ProjectStatus.ACTIVE.value,
                description="E1 stranger-spawn refusal pin",
                project_metadata={},
                relationships={},
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        session.commit()


def _seed_instance(
    eng: Engine,
    *,
    instance_id: str,
    project_id: str,
    agent_id: str = "ari",
    status: str = InstanceStatus.RUNNING.value,
) -> Instance:
    with Session(eng) as session:
        inst = Instance(
            instance_id=instance_id,
            agent_id=agent_id,
            agent_dir=f"/agents/{agent_id}",
            project_id=project_id,
            status=status,
            version=1,
            instance_metadata={},
        )
        session.add(inst)
        session.commit()
        session.refresh(inst)
    return inst


def _seed_alarm_row_and_bound_mapping(
    eng: Engine,
    *,
    source_id: str,
    bound_instance_id: str,
    run_at: datetime,
    project_id: str | None = None,
    label: str = f"self-alarm:{PIN_TAG}",
) -> None:
    """Seed the ``source_configs`` row + the BOUND ``instance_mappings`` row.

    Row shape mirrors the real self-alarm row (seed lineage:
    ``_seed_scheduler_row_and_mapping``) with one difference that IS the
    subject of these pins: ``mapping_metadata`` carries the
    ``bound_to_caller`` binding keys exactly as
    ``self_alarm._bind_schedule_to_caller`` writes them
    (``{"source": "self_alarm", "bound_to_caller": True,
    "caller_instance_id": ...}``).
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    with Session(eng) as session:
        session.add(
            SourceConfigRow(
                source_id=source_id,
                source_type="scheduler",
                name=label,
                config={
                    "run_at": run_at.isoformat(),
                    "instance_mode": "reuse_instance",
                    "agent": "developer",
                    "message": "E1 refusal pin alarm payload",
                    "project_id": project_id,
                    "max_concurrent": 1,
                },
                credentials=None,
                enabled=True,
                autostart=True,
                status="running",
                created_at=now_iso,
                updated_at=now_iso,
            )
        )
        session.commit()

    with Session(eng) as session:
        session.add(
            InstanceMapping(
                mapping_id=str(uuid.uuid4()),
                source_id=source_id,
                external_user_id=source_id,
                agent_instance_id=bound_instance_id,
                agent_id="developer",
                agent_dir="/agents/developer",
                mapping_metadata={
                    "source": "self_alarm",
                    "bound_to_caller": True,
                    "caller_instance_id": bound_instance_id,
                },
                last_message_at=now_iso,
                created_at=now_iso,
            )
        )
        session.commit()


def _tasks_for(eng: Engine, instance_id: str) -> list[Task]:
    with Session(eng) as session:
        return list(session.exec(select(Task).where(Task.instance_id == instance_id)))


def _mq_for(eng: Engine, instance_id: str) -> list[MessageQueue]:
    with Session(eng) as session:
        return list(
            session.exec(select(MessageQueue).where(MessageQueue.instance_id == instance_id))
        )


def _jobitems_for(eng: Engine) -> list[JobItem]:
    return list(Session(eng).exec(select(JobItem)))


def _schedule_executions(eng: Engine, schedule_id: str) -> list[ScheduleExecution]:
    with Session(eng) as session:
        return list(
            session.exec(
                select(ScheduleExecution).where(ScheduleExecution.schedule_id == schedule_id)
            )
        )


def _source_row(eng: Engine, source_id: str) -> SourceConfigRow | None:
    with Session(eng) as session:
        return session.get(SourceConfigRow, source_id)


def _bound_mapping(eng: Engine, source_id: str) -> InstanceMapping | None:
    with Session(eng) as session:
        return (
            session.exec(
                select(InstanceMapping).where(
                    InstanceMapping.source_id == source_id,
                    InstanceMapping.external_user_id == source_id,
                )
            )
        ).first()


# ---------------------------------------------------------------------------
# Production-replica callbacks (seed lineage + registry.py citation)
# ---------------------------------------------------------------------------


def _production_on_complete(source_repo: SQLModelSourceRepository):
    """Replica of the production one-shot completion callback.

    Verbatim behaviour of ``daemon/sources/registry.py`` ``on_complete_callback``
    (:517-526; the closure is not importable — same replica as the seed's
    ``_production_on_complete``):
        self._source_repo.update_source_status(source_id, "stopped")
        self._source_repo.update_source_config(source_id, enabled=False)
    """

    def _on_complete(source_id: str, completed: bool) -> None:
        source_repo.update_source_status(source_id, "stopped")
        source_repo.update_source_config(source_id, enabled=False)

    return _on_complete


def _production_execution_callback(source_repo: SQLModelSourceRepository, events: list):
    """Replica of ``daemon/sources/registry.py`` ``_safe_sync_callback`` (:472-496).

    Identical repo-write behaviour (``triggered`` → ``record_execution_start``;
    ``completed|failed|skipped|queued`` → ``record_execution_complete``),
    MINUS the ``run_in_executor`` thread-pool hop — the pin runs it inline on
    the loop thread so row writes are committed before the drive returns.
    Record-write failures are swallowed exactly as production does, but the
    event dict carries ``record_error`` so a fixture bug cannot hide silently.
    """

    def _cb(
        execution_id: str,
        schedule_id: str,
        status: str,
        instance_id: str | None = None,
        error_message: str | None = None,
    ) -> None:
        event = {
            "execution_id": execution_id,
            "schedule_id": schedule_id,
            "status": status,
            "instance_id": instance_id,
            "error_message": error_message,
            "record_error": None,
        }
        events.append(event)
        try:
            if status == "triggered":
                source_repo.record_execution_start(
                    schedule_id=schedule_id,
                    instance_id=instance_id,
                    execution_id=execution_id,
                )
            elif status in ("completed", "failed", "skipped", "queued"):
                source_repo.record_execution_complete(
                    execution_id=execution_id,
                    status=status,
                    error_message=error_message,
                )
        except Exception as exc:  # noqa: BLE001 — mirrors the production swallow
            event["record_error"] = f"{type(exc).__name__}: {exc}"

    return _cb


# ---------------------------------------------------------------------------
# Hermetic stand-ins
# ---------------------------------------------------------------------------


class _FakeAgentRegistry:
    """Minimal stand-in for the AGENT registry (``daemon.registry``).

    Covers exactly the three call shapes the pinned paths use:
    ``resolve_to_id`` / ``get`` (mapper + scheduler + self_alarm bind) and
    ``exists`` (``scheduling_service.create_schedule`` fail-fast).
    """

    def __init__(self) -> None:
        self._agents = {
            "developer": SimpleNamespace(path="/agents/developer"),
            "ari": SimpleNamespace(path="/agents/ari"),
        }

    def resolve_to_id(self, agent_dir_or_id: str) -> str | None:
        return agent_dir_or_id if agent_dir_or_id in self._agents else None

    def get(self, agent_id: str):
        return self._agents.get(agent_id)

    def exists(self, agent_id: str) -> bool:
        return agent_id in self._agents


@pytest.fixture
def fake_agent_registry(monkeypatch):
    """Patch the three ``get_registry`` binding sites the pins exercise.

    ``daemon/sources/mapper.py`` and ``daemon/sources/adapters/scheduler.py``
    bind the agent registry at MODULE import (``from ..registry import
    get_registry`` / ``from daemon.registry import get_registry``); the
    function-level imports in ``self_alarm._bind_schedule_to_caller`` and
    ``scheduling_service.create_schedule`` resolve at call time. All three
    are function-scoped monkeypatches (auto-restored).
    """
    import daemon.registry as agent_registry_module

    reg = _FakeAgentRegistry()
    monkeypatch.setattr(agent_registry_module, "get_registry", lambda: reg)
    monkeypatch.setattr(mapper_module, "get_registry", lambda: reg)
    monkeypatch.setattr(scheduler_module, "get_registry", lambda: reg)
    return reg


class _StubManager:
    """The manager surface the mapper/adapter fire path touches — with spies.

    ``instances`` (the in-memory fast path), ``_instance_repository`` (the
    slow path), ``spawn_instance_with_mcp`` (the stranger-spawn door —
    RECORDED, never executed), ``enqueue_message_job`` (past-the-mapper
    delivery — recorded so a mutation cannot silently depend on it).
    """

    def __init__(self, engine: Engine, *, instance_repository=None) -> None:
        self.instances: dict = {}
        self._instance_repository = instance_repository or SQLModelInstanceRepository(engine=engine)
        self.spawn_calls: list[dict] = []
        self.enqueue_calls: list[dict] = []

    async def spawn_instance_with_mcp(
        self, *, instance_id: str, agent_id: str, source_type: str | None = None
    ) -> str:
        self.spawn_calls.append(
            {
                "instance_id": instance_id,
                "agent_id": agent_id,
                "source_type": source_type,
            }
        )
        return instance_id

    async def enqueue_message_job(self, **kwargs):
        self.enqueue_calls.append(kwargs)
        return SimpleNamespace(job_id="stub-job", message_id="stub-msg")


class _RaisingInstanceRepo:
    """Unconfirmed-absence fixture: the liveness query ITSELF fails (ADR-11)."""

    def __init__(self, inner: SQLModelInstanceRepository) -> None:
        self._inner = inner
        self.get_calls = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def get(self, instance_id: str):
        self.get_calls += 1
        raise RuntimeError("simulated DB outage during instance liveness query")


class _BindingWriteFailsRepo:
    """Pin-2 failure injection: the binding write (``create_instance_mapping``) RAISES.

    Every other repository call delegates to the real repo. Before raising,
    the stub captures the adapter the arm had already registered+started for
    this source (``registry.get(source_id)``) — the pre-refusal evidence that
    the arm genuinely armed a live adapter before the bind failed.
    """

    def __init__(self, inner: SQLModelSourceRepository, registry: SourceRegistry) -> None:
        self._inner = inner
        self._registry = registry
        self.captured_adapter = None
        self.create_mapping_calls = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def create_instance_mapping(self, *args, **kwargs):
        self.create_mapping_calls += 1
        source_id = kwargs.get("source_id") or (args[0] if args else None)
        try:
            self.captured_adapter = self._registry.get(source_id) if source_id else None
        except Exception:  # noqa: BLE001 — capture is best-effort evidence only
            self.captured_adapter = None
        raise RuntimeError(
            f"simulated binding write failure for {source_id!r} (DEC-21 refusal fixture)"
        )


def _build_pin_adapter(
    engine: Engine,
    manager: _StubManager,
    *,
    source_id: str,
    run_at: datetime,
    project_id: str,
    execution_events: list,
):
    """A REAL ``SchedulerAdapter`` wired to the real repositories (seed lineage:
    ``_build_scheduler_adapter``), configured for the job-queue route
    (truthy ``project_id``) so the fire reaches the DEC-9-cited mapper raise
    site inside ``_route_via_job_queue``."""
    cfg = AdapterSourceConfig(
        source_id=source_id,
        source_type="scheduler",
        name=f"self-alarm:{PIN_TAG}",
        config={
            "run_at": run_at.isoformat(),
            "instance_mode": "reuse_instance",
            "agent": "developer",
            "message": "E1 refusal pin alarm payload",
            "project_id": project_id,
            "max_concurrent": 1,
        },
        credentials={},
        enabled=True,
    )
    adapter = SchedulerAdapter(
        cfg,
        on_message=_noop_on_message,
        execution_callback=_production_execution_callback(
            SQLModelSourceRepository(engine), execution_events
        ),
        on_complete_callback=_production_on_complete(SQLModelSourceRepository(engine)),
        job_queue_service=None,
        source_repo=SQLModelSourceRepository(engine),
        instance_repo=SQLModelInstanceRepository(engine=engine),
        manager=manager,
    )
    # Seed lineage: the semaphore is created in ``start()``; the pin drives
    # ``_run_schedule()`` directly, so it provisions the semaphore itself.
    adapter._execution_semaphore = asyncio.Semaphore(1)
    return adapter


async def _noop_on_message(_msg) -> None:
    return None


@pytest.fixture
def scheduling_service_deps():
    """Save/restore ``scheduling_service._deps`` (module-global singleton).

    The service's dependency holder is a module-level singleton ("single
    manager assumption"); the pin configures it with its own repo + registry
    and MUST restore the previous values so sibling tests in the same pytest
    process are never clobbered.
    """
    saved = (
        scheduling_service._deps.source_repo,
        scheduling_service._deps.source_registry,
        scheduling_service._deps.project_repository,
    )
    try:
        yield scheduling_service
    finally:
        (
            scheduling_service._deps.source_repo,
            scheduling_service._deps.source_registry,
            scheduling_service._deps.project_repository,
        ) = saved


async def _evict_all_adapters(registry: SourceRegistry) -> None:
    """Teardown: evict + stop every adapter this pin may have left registered.

    At base (pin 2) the arm SUCCEEDS best-effort, so a live adapter with a
    45-min-future run_at survives the failed assertion — it must be drained
    before the loop closes (seed lineage: bounded teardown, nothing leaks).
    """
    for source_id in list(getattr(registry, "_adapters", {})):
        try:
            await scheduling_service._evict_and_stop_adapter(source_id)
        except Exception:  # noqa: BLE001 — teardown best-effort
            try:
                registry.unregister(source_id)
            except Exception:  # noqa: BLE001
                pass


# ===========================================================================
# THE PINS
# ===========================================================================


class TestE1RefusesStrangerSpawn:
    """E1 — the stranger-spawn refusal, both surfaces (DEC-5 + DEC-21).

    Plan anchors: decisions.md DEC-5 (mapper refusal, confirmed-absence only),
    DEC-9 CRIT-1 (the fifth terminal outcome ``refused_permanent`` + the
    narrow catch + the consumption gate), DEC-21 (arm-time refuse-to-arm +
    F1 row disposition + Blocking-2 adapter eviction), plan-overview.md §(e)
    rows 5–6.
    """

    async def test_bound_caller_confirmed_gone_refuses_instead_of_spawning(
        self, pin_engine: Engine, fake_agent_registry
    ):
        """Fire-time E1: CONFIRMED-gone bound caller ⇒ typed refusal, NO spawn.

        RED-MUTATION SPEC (plan-overview.md §(e) row 5, VERBATIM):
        "**Two RED mutations, both required.** (a) Remove the `bound_to_caller`
        refusal in `mapper.py:336-344` (restore delete+spawn) — the fixture
        must be a **CONFIRMED-absence** one (instance absent from memory
        **and** DB, successful query → `False`). **(b) CRIT-1 part 5 — remap
        the refusal to `failed` instead of `refused_permanent`** (in the
        narrow catch site, set `_fire_outcome = "failed"`, or route the typed
        refusal into the blanket `except`) → the one-shot is **not consumed**
        → the 5s spin reappears"

        GREEN contract (DEC-9 CRIT-1 Part 4 + plan §(e) row 5): the mapper
        RAISES the dedicated typed refusal; NO ``spawn_instance_with_mcp``
        call; the mapping row is NOT deleted; the schedule's execution row is
        ``failed``; AND the consumption: the one-shot IS consumed (source row
        stopped / ``enabled=False``, ``_is_one_time_executed is True``) with
        the refusal reason in the ``failed`` row's ``error_message``.
        Contrast leg (ADR-11): a DB-error liveness query must NOT refuse —
        return-existing, no spawn, mapping intact.
        """
        # -- fixture: a bound schedule whose caller is CONFIRMED gone -------
        source_id = f"{PIN_TAG}-src-{uuid.uuid4().hex[:8]}"
        gone_instance_id = str(uuid.uuid4())  # bound caller — NEVER seeded
        project_id = str(uuid.uuid4())
        _seed_project(pin_engine, project_id)
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)
        _seed_alarm_row_and_bound_mapping(
            pin_engine,
            source_id=source_id,
            bound_instance_id=gone_instance_id,
            run_at=run_at,
            project_id=project_id,
        )

        source_repo = SQLModelSourceRepository(pin_engine)
        manager = _StubManager(pin_engine)  # instances={} → absent from memory
        execution_events: list = []
        adapter = _build_pin_adapter(
            pin_engine,
            manager,
            source_id=source_id,
            run_at=run_at,
            project_id=project_id,
            execution_events=execution_events,
        )

        # -- RED-for-the-right-reason guard ---------------------------------
        refusal_type, refusal_home = _find_refusal_type()
        assert refusal_type is not None, (
            "E1 RED-for-the-right-reason: the dedicated typed refusal type "
            f"'{_REFUSAL_TYPE_NAME}' (plan decisions.md DEC-9 CRIT-1 Part 3: "
            "'a dedicated type (not a bare Exception subclass)') is not "
            f"implemented on this tree (searched: {', '.join(_REFUSAL_TYPE_HOMES)}). "
            "The post-fix refusal behaviour is absent — that absence IS this "
            "pin's expected base failure."
        )

        # -- ACT 1a: the mapper-level refusal (the typed door, DEC-5) -------
        mapper = InstanceMapper(source_repo, manager)
        with pytest.raises(refusal_type) as excinfo:
            await mapper.get_or_create_instance(
                source_id=source_id,
                external_user_id=source_id,
                agent_id="developer",
                force_new=False,
            )
        # The typed error carries the refusal reason as its message
        # (DEC-9 CRIT-1 Part 3) so the row's error_message needs no
        # string-matching.
        assert str(excinfo.value), (
            "the typed refusal must carry the refusal reason as its "
            "message/attribute (DEC-9 CRIT-1 Part 3)"
        )
        assert manager.spawn_calls == [], (
            "stranger spawn: the mapper spawned a fresh instance for a "
            "CONFIRMED-gone bound caller (mutation (a) shape — the "
            "delete+spawn fall-through was taken)"
        )
        assert _bound_mapping(pin_engine, source_id) is not None, (
            "the refusal must NOT delete the mapping row (DEC-5: 'do not "
            "delete the mapping, do not spawn')"
        )

        # -- ACT 1b: the adapter fire ⇒ refused_permanent end-state --------
        # Drive the REAL fire path (_run_schedule → _emit_scheduled_message →
        # _execute_run → _route_via_job_queue → mapper). Post-fix the narrow
        # catch classifies the refusal as refused_permanent: consume VISIBLY.
        # The call_later stop timer bounds mutation (b)'s 5s re-fire spin so
        # the pin fails on the consumption ASSERTION, not on a timeout.
        loop = asyncio.get_running_loop()
        stop_timer = loop.call_later(9.0, adapter._stop_event.set)
        try:
            await asyncio.wait_for(adapter._run_schedule(), timeout=12.0)
        finally:
            stop_timer.cancel()

        exec_rows = _schedule_executions(pin_engine, source_id)
        failed_rows = [e for e in exec_rows if e.status == "failed"]
        assert failed_rows, (
            "no 'failed' execution row was written for the refused delivery "
            f"(schedule_executions statuses: {[e.status for e in exec_rows]}) — "
            "the refusal must complete the execution row as FAILED with the "
            "refusal reason (DEC-9 CRIT-1 narrow catch)"
        )
        assert failed_rows[-1].error_message, (
            "the failed execution row must carry the refusal reason in "
            "error_message (DEC-9 CRIT-1 Part 1: refused_permanent = consume "
            "+ a failed row carrying the refusal reason)"
        )
        # CRIT-1 Part 4 — the CONSUMPTION assertion (distinguishes the
        # four-value vocabulary from the five-value one).
        assert adapter._is_one_time_executed is True, (
            "the one-shot was NOT consumed on the refusal leg — a provably "
            "dead target stays armed and re-fires every 5s (mutation (b) "
            "shape: the refusal was remapped to 'failed' instead of "
            "'refused_permanent')"
        )
        row_after = _source_row(pin_engine, source_id)
        assert row_after is not None
        assert row_after.status == "stopped", (
            f"expected the consumed one-shot row status='stopped', got "
            f"{row_after.status!r} (consumption replica of registry.py "
            ":517-526 did not run)"
        )
        assert row_after.enabled is False, (
            f"expected enabled=False after the consumed one-shot, got "
            f"{row_after.enabled!r}"
        )
        assert manager.spawn_calls == [], (
            "stranger spawn through the adapter fire path — the refusal did "
            "not hold at the _route_via_job_queue mapper site"
        )
        assert _bound_mapping(pin_engine, source_id) is not None, (
            "the adapter-level refusal must not delete the mapping row either"
        )

        # -- ACT 2: UNCONFIRMED absence must NOT refuse (ADR-11) ------------
        # The liveness QUERY fails (except → return True, mapper.py:198-210
        # preserved verbatim) ⇒ return-existing, no refusal, no spawn, and
        # the mapping is never deleted.
        flaky_manager = _StubManager(
            pin_engine,
            instance_repository=_RaisingInstanceRepo(SQLModelInstanceRepository(engine=pin_engine)),
        )
        flaky_mapper = InstanceMapper(source_repo, flaky_manager)
        returned_id = await flaky_mapper.get_or_create_instance(
            source_id=source_id,
            external_user_id=source_id,
            agent_id="developer",
            force_new=False,
        )
        assert returned_id == gone_instance_id, (
            "a DB-error liveness query must return the EXISTING mapped "
            "instance (fail-open-to-return-existing preserved, ADR-11)"
        )
        assert flaky_manager.spawn_calls == [], (
            "a DB-error liveness query must NEVER reach the spawn "
            "fall-through (ADR-11: uncertainty must NOT refuse and must not "
            "cascade into a forced re-spawn)"
        )
        assert _bound_mapping(pin_engine, source_id) is not None, (
            "the unconfirmed-absence leg must not delete the mapping"
        )

    async def test_rebuilt_mapping_preserves_binding_keys(
        self, pin_engine: Engine, fake_agent_registry
    ):
        """DEC-5 item 3 — a REBUILT mapping must PRESERVE the binding keys.

        MUTATION SPEC (leader ruling ADJ-3, VERBATIM): "RED mutation = restore
        the wholesale replace (i.e. if the implementation goes back to
        replacing `mapping_metadata` wholesale, this pin must go RED)."
        Expected RED symptom of that mutation: the rebuilt
        ``instance_mappings.mapping_metadata`` holds only the mapper's fresh
        ``{"source_id", "external_user_id"}`` (plus any unrelated
        ``extra_mapping_metadata``) and NO LONGER carries ``source`` /
        ``bound_to_caller`` / ``caller_instance_id`` → the binding-keys
        assertion fails, and the failure message dumps the prior mapping's
        metadata (``before=``) beside the rebuilt one (``after=``).

        Plan §(e) row 5 sibling wording: "mutate `:368-377` to drop
        `bound_to_caller` → RED".

        DEC-5 item 3 (canonical): "at the metadata-construction site
        (`:368-377`), carry `bound_to_caller` / `caller_instance_id` /
        `source` from the prior mapping's metadata into the rebuilt mapping."
        The key spellings are the ones ``_bind_schedule_to_caller`` stamps
        (``daemon/tools/self_alarm.py:938-942``)::

            {"source": "self_alarm", "bound_to_caller": True,
             "caller_instance_id": <caller instance id>}

        WHY the rebuild matters (DEC-5 rationale): the fire-time refusal is
        gated on ``mapping["metadata"].get("bound_to_caller")`` being TRUTHY —
        a mapping whose binding keys were silently dropped LOOKS unbound, so
        the refusal can never fire for that row and the stranger-spawn door
        re-opens ("Without (3), the `force_new` path would silently drop the
        binding, re-opening E1 for the next fire"). The repo-side upsert stays
        untouched (DEC-5 item 2 — ``create_instance_mapping`` keeps replacing
        ``mapping_metadata`` wholesale); the preservation belongs at the
        mapper's own metadata-construction site.

        The rebuild trigger is ``force_new=True`` — the path DEC-5 names: the
        prior (BOUND) mapping is deleted and the mapping is rebuilt at the
        metadata-construction site. (The stale-mapping branch cannot exercise
        a rebuild post-fix: DEC-5 item 1 REFUSES there instead.)

        The pin proves its own fixture: the rebuild preconditions (exactly one
        spawn, the row retargeted at the rebuilt instance) are asserted BEFORE
        the binding-keys assertion, so a failure in the binding-keys assertion
        is evidence about the rebuild — not about a broken fixture.
        """
        # -- fixture: a BOUND mapping row (as _bind_schedule_to_caller writes it)
        source_id = f"{PIN_TAG}-rebuild-{uuid.uuid4().hex[:8]}"
        caller_instance_id = str(uuid.uuid4())
        project_id = str(uuid.uuid4())
        _seed_project(pin_engine, project_id)
        _seed_instance(
            pin_engine,
            instance_id=caller_instance_id,
            project_id=project_id,
            agent_id="ari",
        )
        run_at = datetime.now(timezone.utc) + timedelta(seconds=1.2)
        _seed_alarm_row_and_bound_mapping(
            pin_engine,
            source_id=source_id,
            bound_instance_id=caller_instance_id,
            run_at=run_at,
            project_id=project_id,
        )

        source_repo = SQLModelSourceRepository(pin_engine)
        manager = _StubManager(pin_engine)
        mapper = InstanceMapper(source_repo, manager)

        # BEFORE — the prior mapping's metadata, captured as a PLAIN dict (the
        # ORM object must not be read after the rebuild deleted its row).
        prior = _bound_mapping(pin_engine, source_id)
        assert prior is not None, (
            "fixture precondition: the bound mapping row is missing — the "
            "rebuild scenario was never set up"
        )
        before_meta = dict(prior.mapping_metadata or {})

        # -- the rebuild: force_new deletes the BOUND mapping and rebuilds it
        # at the metadata-construction site. The extra mapping metadata is
        # UNRELATED to the binding ON PURPOSE: it proves the assertion is not
        # satisfied by "some metadata got carried" — only the PRESERVED
        # binding keys satisfy it.
        await mapper.get_or_create_instance(
            source_id=source_id,
            external_user_id=source_id,
            agent_id="developer",
            force_new=True,
            extra_mapping_metadata={"channel_id": "C0-E1-PIN"},
        )

        # -- the rebuild really happened (the row was retargeted) ----------
        assert len(manager.spawn_calls) == 1, (
            "fixture precondition: the rebuild path did not run — expected "
            f"exactly one spawn, got {manager.spawn_calls!r}"
        )
        rebuilt = _bound_mapping(pin_engine, source_id)
        assert rebuilt is not None, (
            "the rebuilt mapping row is missing — the rebuild path did not run"
        )
        after_meta = dict(rebuilt.mapping_metadata or {})
        assert rebuilt.agent_instance_id == manager.spawn_calls[0]["instance_id"], (
            "fixture precondition: the mapping was not retargeted at the "
            "rebuilt instance — the rebuild path did not run"
        )

        # -- THE PIN: the binding keys survived the rebuild -----------------
        binding_keys = ("source", "bound_to_caller", "caller_instance_id")
        missing = [k for k in binding_keys if k not in after_meta]
        assert not missing, (
            f"E1 RED: the rebuilt instance-mapping DROPPED the binding keys "
            f"{missing} — the mapper replaced `mapping_metadata` wholesale "
            "instead of carrying the binding keys forward (DEC-5 item 3: at "
            "the metadata-construction site :368-377 carry `bound_to_caller` "
            "/ `caller_instance_id` / `source` from the prior mapping's "
            "metadata into the rebuilt mapping). "
            f"before={before_meta!r} after={after_meta!r}"
        )
        # Truthiness, not identity: DEC-5 item 1 gates the refusal on
        # `mapping["metadata"].get("bound_to_caller")` being truthy.
        assert after_meta.get("bound_to_caller"), (
            "E1 RED: the rebuilt mapping is not marked as bound to its caller "
            "— the fire-time refusal (gated on a truthy `bound_to_caller`) "
            f"can never fire for this row again. before={before_meta!r} "
            f"after={after_meta!r}"
        )
        assert after_meta.get("caller_instance_id") == caller_instance_id, (
            "E1 RED: the rebuilt mapping lost the caller it is bound to "
            f"(expected {caller_instance_id!r}, got "
            f"{after_meta.get('caller_instance_id')!r}). before={before_meta!r} "
            f"after={after_meta!r}"
        )
        assert after_meta.get("source") == "self_alarm", (
            "E1 RED: the rebuilt mapping lost its `source` provenance key "
            f"(expected 'self_alarm', got {after_meta.get('source')!r}). "
            f"before={before_meta!r} after={after_meta!r}"
        )

    async def test_binding_write_failure_refuses_to_arm_and_leaves_the_schedule_unarmed(
        self,
        pin_engine: Engine,
        fake_agent_registry,
        scheduling_service_deps,
    ):
        """Arm-time E1 (DEC-21 refuse-to-arm): binding-write failure ⇒ NO arm.

        RED-MUTATION SPEC (plan-overview.md §(e) row 6, VERBATIM):
        "**Restore the best-effort `return None` path** (`self_alarm.py:950`)
        — the arm proceeds with `wake_target_bound:false`"
        RED symptom: the arm SUCCEEDS and the schedule row is enabled → the
        unbound alarm is armed and can fire.
        Restore → GREEN: no arm + a typed error raised naming the binding
        failure + the schedule row is NOT armed (status='stopped',
        enabled=False) + the restated F1/Blocking-2 named assertion — the
        adapter is evicted + stopped (registry-unregistered, not running),
        no mapping exists for the caller, a forced fire attempt on that row
        yields no delivery (no Task, no MessageQueue row, no JobItem, no
        delivery-asserting schedule_executions row), and no spawn occurs
        (neither spawn_instance_with_mcp nor the no-mapping fall-through
        mapper.py:352-360 is reached).

        Named assertion "a refused arm cannot fire" (decisions.md DEC-21,
        restated per Blocking 2) is pinned here.
        """
        # -- fixture ---------------------------------------------------------
        project_id = str(uuid.uuid4())
        caller_instance_id = str(uuid.uuid4())
        _seed_project(pin_engine, project_id)
        # The caller is a real RUNNING ari instance (project_id must resolve —
        # D11 resolve-FIRST gate in _arm_self_alarm).
        _seed_instance(
            pin_engine,
            instance_id=caller_instance_id,
            project_id=project_id,
            agent_id="ari",
        )

        source_repo = SQLModelSourceRepository(pin_engine)
        manager = _StubManager(pin_engine)  # spawn spy for the no-spawn assert
        instance_repo = SQLModelInstanceRepository(engine=pin_engine)

        # A REAL registry so the eviction assertion is about THE registry
        # every fire path resolves adapters through.
        registry = SourceRegistry(
            source_repo=source_repo,
            manager=manager,
            job_queue_service=None,
            instance_repo=instance_repo,
        )
        scheduling_service.configure(
            source_repo=source_repo, source_registry=registry
        )

        # The binding write FAILS (the DEC-21 failure injection); the stub
        # captures the adapter the arm had already registered+started.
        binding_repo = _BindingWriteFailsRepo(source_repo, registry)
        bare_label = f"pin-{uuid.uuid4().hex[:6]}"
        effective_label = effective_label_for(bare_label)

        # -- RED-for-the-right-reason guard ----------------------------------
        refusal_type, refusal_home = _find_refusal_type()
        assert refusal_type is not None, (
            "E1 arm-time RED-for-the-right-reason: the dedicated typed "
            f"refusal type '{_REFUSAL_TYPE_NAME}' (plan decisions.md DEC-21: "
            "'REFUSE to arm ... surface a clear TYPED error naming the "
            "binding failure') is not implemented on this tree (searched: "
            f"{', '.join(_REFUSAL_TYPE_HOMES)}). The post-fix refuse-to-arm "
            "behaviour is absent — that absence IS this pin's expected base "
            "failure."
        )

        try:
            # -- the refused arm ------------------------------------------
            with pytest.raises(refusal_type) as excinfo:
                await _arm_self_alarm(
                    service=scheduling_service,
                    source_repo=binding_repo,  # the binding write RAISES
                    instance_repository=instance_repo,
                    project_repository=None,
                    current_instance_id=caller_instance_id,
                    agent_id="ari",  # the runtime ari-only gate
                    label=bare_label,
                    when_or_every="45m",  # ≥ the 30-minute floor
                    message_to_self="E1 arm-time refusal pin",
                    link_board_item=None,
                )
            assert "bind" in str(excinfo.value).lower(), (
                "the typed refusal must NAME the binding failure "
                f"(DEC-21: 'a clear TYPED error naming the binding failure'); "
                f"got: {str(excinfo.value)!r}"
            )

            # -- F1: the row is left DISABLED and UNARMED, NOT deleted -----
            row = source_repo.get_source_config_by_name(effective_label)
            assert row is not None, (
                "F1: the already-created schedule row must NOT be deleted — "
                "it stays visible with its retry intent"
            )
            assert row.status == "stopped", (
                f"F1: expected the refused row status='stopped' (the "
                f"sanctioned not-armed transition, registry.py:522-523), got "
                f"{row.status!r}"
            )
            assert row.enabled is False, (
                f"F1: expected enabled=False on the refused row, got "
                f"{row.enabled!r} (row flags alone are necessary; Blocking 2 "
                "adds the adapter eviction below)"
            )

            # -- Blocking 2: the adapter is EVICTED + STOPPED ---------------
            assert registry.get(row.source_id) is None, (
                "the adapter must be EVICTED from the registry (Blocking 2: "
                "row flags alone are insufficient — enabled is read only at "
                "boot registry.py:291 and the loop never re-reads it "
                "scheduler.py:519-520, so a still-registered adapter would "
                "fire the unbound alarm on its next tick)"
            )
            captured = binding_repo.captured_adapter
            assert captured is not None, (
                "fixture precondition failed: the arm did NOT have a live "
                "registered adapter at bind time — create_schedule's "
                "rebuild+register+start did not run before the binding write"
            )
            assert captured._stop_event.is_set(), (
                "the captured adapter must be STOPPED (its _stop_event set by "
                "the refusal path's adapter.stop()) — a merely unregistered "
                "but running loop is exactly the Blocking-2 hazard"
            )
            assert captured._status == SourceStatus.STOPPED, (
                f"expected adapter _status STOPPED after the refused arm, got "
                f"{captured._status!r}"
            )

            # -- no mapping exists for the caller ---------------------------
            assert source_repo.get_instance_mapping(row.source_id, row.source_id) is None, (
                "no mapping row may exist after a refused arm — the binding "
                "write failed and the refusal must not leave a partial bind"
            )

            # -- "a refused arm cannot fire": forced fire attempt -----------
            # The sanctioned fire seam is the registry: every fire path
            # resolves the adapter through registry.get(). Post-eviction the
            # attempt finds nothing and starts nothing.
            started_again = await registry.start_adapter(row.source_id)
            assert started_again is False, (
                "a refused arm cannot fire: registry.start_adapter on the "
                "evicted source must find no adapter and start nothing"
            )

            # -- zero delivery artifacts ------------------------------------
            assert _tasks_for(pin_engine, caller_instance_id) == [], (
                "a refused arm must not produce a Task row for the caller"
            )
            assert _mq_for(pin_engine, caller_instance_id) == [], (
                "a refused arm must not produce a MessageQueue row"
            )
            assert _jobitems_for(pin_engine) == [], (
                "a refused arm must not produce a JobItem"
            )
            assert _schedule_executions(pin_engine, row.source_id) == [], (
                "a refused arm must not produce any schedule_executions row "
                "(nothing fired)"
            )

            # -- no spawn: neither spawn door was reached --------------------
            assert manager.spawn_calls == [], (
                "no spawn may occur after a refused arm — neither "
                "spawn_instance_with_mcp nor the no-mapping fall-through "
                "(mapper.py:352-360) may be reached"
            )
        finally:
            # At base the arm SUCCEEDED best-effort (RED) — drain whatever
            # adapter is still registered+running before the loop closes.
            await _evict_all_adapters(registry)
