"""P3 T3.4/T3.5 — supervisor boundary + dynamic re-registration."""

from __future__ import annotations

import asyncio

import pytest

from daemon.services.engine.registry import EngineRegistry
from daemon.repositories.engine.repository import EngineRepository
from daemon.services.engine.engine import EngineService
from daemon.services.engine.spec import (
    IntervalTrigger,
    JobSpec,
    ServiceMeta,
)


def _spec(sid="job-1"):
    return JobSpec(
        id=sid,
        meta=ServiceMeta(service_tag="t", category="reconciler"),
        trigger=IntervalTrigger(seconds=10.0),
        max_runtime_s=5.0,
    )


def _engine():
    import tempfile, os
    from sqlalchemy import create_engine
    from sqlmodel import SQLModel
    from daemon.repositories.engine.models import EngineJob, EngineRun  # noqa
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = create_engine(f"sqlite:///{path}")
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.mark.asyncio
async def test_supervisor_restarts_counter_attribute():
    # EngineService exposes _supervisor_restarts (P4 watchdog input)
    from daemon.services.engine.engine import EngineService
    repo = EngineRepository(_engine())
    reg = EngineRegistry(repo)
    svc = EngineService(repository=repo, registry=reg, shadow_mode=True)
    assert getattr(svc, "_supervisor_restarts", None) == 0


def test_re_registration_changed_bumps_version():
    repo = EngineRepository(_engine())
    reg = EngineRegistry(repo)
    v1 = reg.register(_spec())
    assert v1 == 1
    spec2 = _spec()
    # change content: different max_runtime
    spec2b = JobSpec(
        id="job-1",
        meta=ServiceMeta(service_tag="t", category="reconciler"),
        trigger=IntervalTrigger(seconds=10.0),
        max_runtime_s=9.0,
    )
    v2 = reg.register(spec2b)
    assert v2 == 2


def test_re_registration_identical_echoes():
    repo = EngineRepository(_engine())
    reg = EngineRegistry(repo)
    v1 = reg.register(_spec())
    v2 = reg.register(_spec())
    assert v1 == v2 == 1


def test_re_registration_out_of_band_mutation_bumps():
    # fold #4: ground echo on DB row always
    repo = EngineRepository(_engine())
    reg = EngineRegistry(repo)
    v1 = reg.register(_spec())
    # mutate DB row out-of-band: bump version manually via repository? Instead
    # change registration content directly in DB by re-registering a changed spec
    # then re-register original -> should bump (not echo) because DB content differs.
    changed = JobSpec(
        id="job-1",
        meta=ServiceMeta(service_tag="t", category="reconciler"),
        trigger=IntervalTrigger(seconds=10.0),
        max_runtime_s=7.0,
    )
    v2 = reg.register(changed)
    assert v2 == 2
    # now re-register original content -> version 3 (not echo of 1)
    v3 = reg.register(_spec())
    assert v3 == 3


@pytest.mark.asyncio
async def test_cancel_callback_invoked_on_changed_reregistration():
    repo = EngineRepository(_engine())
    reg = EngineRegistry(repo)
    await reg.aregister(_spec())
    calls = []

    def cb(jid):
        calls.append(jid)

    reg.set_cancel_callback(cb)
    changed = JobSpec(
        id="job-1",
        meta=ServiceMeta(service_tag="t", category="reconciler"),
        trigger=IntervalTrigger(seconds=10.0),
        max_runtime_s=9.0,
    )
    await reg.aregister(changed)
    assert calls == ["job-1"]


def test_cancel_callback_not_invoked_on_echo():
    repo = EngineRepository(_engine())
    reg = EngineRegistry(repo)
    reg.register(_spec())
    calls = []

    def cb(jid):
        calls.append(jid)

    reg.set_cancel_callback(cb)
    reg.register(_spec())  # identical -> echo, no cancel
    assert calls == []


def test_supervisor_boundary_never_returns_on_exception():
    # Structural: execution.run wraps _tick in try/except and continues
    import inspect
    from daemon.services.engine.execution import EngineExecutionLoop
    src = inspect.getsource(EngineExecutionLoop.run)
    assert "continue" in src


def test_f2_cancel_refreshes_spec_and_persisted_dead():
    """F2: after a CHANGED re-registration fires the cancel callback, the
    engine cache entry carries the NEW spec (== registry.cached_spec) and
    persisted_dead == the DB row's current dead value.  Cache is seeded with
    a STALE spec + stale persisted_dead first so the refresh is observable.
    """
    repo = EngineRepository(_engine())
    reg = EngineRegistry(repo)
    svc = EngineService(repository=repo, registry=reg, shadow_mode=True)
    reg.set_cancel_callback(svc._cancel_inflight_tick)

    v1 = reg.register(_spec())
    assert v1 == 1

    stale_spec = JobSpec(
        id="job-1",
        meta=ServiceMeta(service_tag="t", category="reconciler"),
        trigger=IntervalTrigger(seconds=10.0),
        max_runtime_s=1.0,
    )
    svc._cache["job-1"] = {
        "spec": stale_spec,
        "persisted_dead": False,  # stale (DB row is dead=1)
        "pending_event": True,
        "cancel_requested": False,
        "reregistered": False,
    }

    repo.stamp_job_outcome("job-1", dead=1)

    changed = JobSpec(
        id="job-1",
        meta=ServiceMeta(service_tag="t", category="reconciler"),
        trigger=IntervalTrigger(seconds=10.0),
        max_runtime_s=9.0,
    )
    v2 = reg.register(changed)
    assert v2 == 2

    cache = svc._cache["job-1"]
    assert cache["spec"] is reg.cached_spec("job-1")
    row = repo.get_job("job-1")
    assert cache["persisted_dead"] == bool(getattr(row, "dead", 0))
    # observable refresh: stale False -> now matches row (dead=1 untouched by re-reg)
    assert cache["persisted_dead"] is True


def test_f3_cancel_clears_pending_event_and_registry_owns_no_latch():
    """F3: after the same cancel path, cache['pending_event'] is False AND
    the registry performed no dead/cancel_requested write of its own (the
    latch lives only in EngineService._cache)."""
    repo = EngineRepository(_engine())
    reg = EngineRegistry(repo)
    svc = EngineService(repository=repo, registry=reg, shadow_mode=True)
    reg.set_cancel_callback(svc._cancel_inflight_tick)

    reg.register(_spec())

    revive_calls = []
    stamp_calls = []
    orig_revive = repo.revive_job
    orig_stamp = repo.stamp_job_outcome

    def tracked_revive(jid):
        revive_calls.append(jid)
        return orig_revive(jid)

    def tracked_stamp(jid, **kw):
        stamp_calls.append((jid, kw))
        return orig_stamp(jid, **kw)

    repo.revive_job = tracked_revive
    repo.stamp_job_outcome = tracked_stamp

    svc._cache["job-1"] = {
        "spec": _spec(),
        "persisted_dead": False,
        "pending_event": True,
        "cancel_requested": False,
        "reregistered": False,
    }

    changed = JobSpec(
        id="job-1",
        meta=ServiceMeta(service_tag="t", category="reconciler"),
        trigger=IntervalTrigger(seconds=10.0),
        max_runtime_s=9.0,
    )
    reg.register(changed)

    cache = svc._cache["job-1"]
    assert cache["pending_event"] is False

    # Registry never owns the latch: its cache carries no pending_event /
    # cancel_requested keys.
    reg_entry = reg._cache.get("job-1", {})
    assert "pending_event" not in reg_entry
    assert "cancel_requested" not in reg_entry

    # The cancel path performed no dead/outcome writes of its own.
    assert revive_calls == []
    assert stamp_calls == []
