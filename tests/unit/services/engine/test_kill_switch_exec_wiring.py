"""Kill-switch EXECUTION-path wiring (Engine Arc — the engine-core inertness defect).

The defect this file closes: ``EngineSwitchResolver.resolve_job`` was armed at
boot (``daemon/api.py``) but NEVER read by the execution path — the documented
engine-wide mass-rollback lever was boot-scoped (restart-scoped) only.

Plan semantics implemented here (authority:
``.agents/shared/planning/engine-arc-phase1/phase3-plan.md`` §3):

* **D3.6** — the engine-wide switch is read **at tick time** through the
  resolution ladder: per-job ``kill_switch_key`` (env/config tier) →
  ``ENSEMBLE_ENGINE_ENABLED`` → ``ENSEMBLE_ENGINE_SHADOW_MODE`` → spec.
  A flip takes effect on the **next tick**, no restart.
* **D3.5** — fail-to-legacy polarity: an unreadable engine-wide switch
  resolves to "engine does not own execution" (the legacy path keeps
  running); a per-job ``kill_switch_key`` that is unreadable resolves to
  "job skipped, counted as KILL_SWITCHED".
* **D3.9** — the supervisor boundary is preserved: a tripped switch never
  kills the tick loop (the loop re-enters after a bounded backoff).
* **D3.12** — kill-switch reads are pure functions over a resolver INJECTED
  into the engine; the resolver caches nothing; the tick NEVER reads
  ``os.environ`` itself. Every test below is therefore ENV-FREE.

Vocabulary (D2.6 closed skip-reason set, ``daemon/services/engine/state.py``):
outcome ``SKIPPED_KILL_SWITCHED`` / reason token ``KILL_SWITCHED``.
"""

from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from daemon.repositories.engine.models import EngineRun
from daemon.repositories.engine.repository import EngineRepository
from daemon.services.engine.engine import EngineService
from daemon.services.engine.killswitch import EngineSwitchResolver
from daemon.services.engine.registry import EngineRegistry
from daemon.services.engine.spec import IntervalTrigger, JobSpec, ServiceMeta

EXECUTION = Path(__file__).resolve().parents[4] / "daemon" / "services" / "engine" / "execution.py"
EXECUTION_SRC = EXECUTION.read_text(encoding="utf-8")
API = Path(__file__).resolve().parents[4] / "daemon" / "api.py"
API_SRC = API.read_text(encoding="utf-8")

ENGINE_KEY = "SERVICES_WIDGET_SWEEP_USE_ENGINE"
OTHER_KEY = "SERVICES_OTHER_SWEEP_USE_ENGINE"
ENGINE_WIDE_KEY = "ENSEMBLE_ENGINE_ENABLED"


# ── harness ──────────────────────────────────────────────────────────────

def _db_engine() -> Any:
    eng = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    return eng


def _cfg(interval: float = 1.0) -> Any:
    class _EngineCfg:
        enabled = True
        tick_resolution_seconds = 0.05
        tick_workers = 2
        boot_pass_deadline_seconds = 5.0
        extend_ceiling_seconds = 30.0
        retry_backoff_cap_seconds = 30.0
        shadow_mode = False

    class _Cfg:
        engine = _EngineCfg()

    return _Cfg()


def _spec(job_id: str, kill_switch_key: str | None, interval: float = 1.0) -> JobSpec:
    return JobSpec(
        id=job_id,
        meta=ServiceMeta(
            service_tag=job_id,
            category="test",
            service_name=job_id,
        ),
        trigger=IntervalTrigger(seconds=interval),
        max_runtime_s=30.0,
        kill_switch_key=kill_switch_key,
    )


class _Harness:
    """An engine with an INJECTED resolver over a MUTABLE source (D3.12).

    The source dict is the "environment" the resolver reads. Mutating it
    between ticks IS the runtime flip — no restart, no os.environ.
    """

    def __init__(self, jobs: list[JobSpec], shadow: bool = False) -> None:
        self.source: dict[str, Any] = {}
        self.resolver = EngineSwitchResolver(resolver=self.source.get)
        self.db = _db_engine()
        self.repo = EngineRepository(self.db)
        self.registry = EngineRegistry(self.repo)
        self.cfg = _cfg()
        self.cfg.engine.shadow_mode = shadow
        self.engine = EngineService(
            self.repo,
            self.registry,
            self.cfg.engine,
            shadow_mode=shadow,
            switch_resolver=self.resolver,
        )
        self.calls: dict[str, int] = {j.id: 0 for j in jobs}

    async def build(self, jobs: list[JobSpec]) -> None:
        for job in jobs:
            await self.registry.aregister(job)
        # Hydrate the engine's RAM cache WITHOUT starting the tick loop — the
        # dispatch-path proofs drive `_dispatch_one` directly so they are
        # deterministic (no due-clock wait, no sleep).
        await self.engine._hydrate()

    def executor(self, job_id: str):
        def _run() -> dict[str, Any]:
            self.calls[job_id] = self.calls.get(job_id, 0) + 1
            return {"ok": True}

        return _run

    def arm(self, job_id: str) -> None:
        self.engine.set_executor(job_id, self.executor(job_id))

    def outcomes(self, job_id: str) -> list[str]:
        with Session(self.db) as s:
            rows = list(
                s.exec(
                    select(EngineRun)
                    .where(EngineRun.job_id == job_id)
                    .order_by(EngineRun.started_at)
                ).all()
            )
            return [r.outcome for r in rows]

    def skip_reasons(self, job_id: str) -> list[Any]:
        with Session(self.db) as s:
            rows = list(
                s.exec(
                    select(EngineRun)
                    .where(EngineRun.job_id == job_id)
                    .order_by(EngineRun.started_at)
                ).all()
            )
            return [r.skip_reason for r in rows]

    async def dispatch(self, job_id: str) -> None:
        """Drive ONE dispatch through the real tick path (no due-clock wait)."""
        loop = self.engine._execution_loop
        await loop._dispatch_one(job_id, datetime.now(timezone.utc))


# ── wiring pins (the defect itself) ──────────────────────────────────────

def test_resolver_is_injected_into_the_engine_at_boot():
    """D3.12 — the boot wiring THREADS the resolver into the engine."""
    assert "switch_resolver=_engine_switch," in API_SRC
    assert "_engine_switch = EngineSwitchResolver(resolver=os.environ.get)" in API_SRC


def test_tick_path_consults_the_resolver_on_every_dispatch():
    """D3.6 — the consult lives in the DISPATCH path, not in the boot path."""
    assert "_resolve_switch(spec)" in EXECUTION_SRC
    # The consult is unconditional in the dispatch body (no boot-scoped gate).
    assert "should_run, kill_reason = self._resolve_switch(spec)" in EXECUTION_SRC


def test_tick_never_reads_os_environ_directly():
    """D3.12 — the tick reads the INJECTED resolver, never the ambient env.

    AST-based (not a substring grep): a docstring that NAMES ``os.environ``
    is not a read of it. The pin looks for an actual attribute access.
    """
    tree = ast.parse(EXECUTION_SRC)
    reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "environ"
    ]
    assert reads == [], "the tick path reads os.environ directly (D3.12)"


def test_resolver_absent_means_no_switch_opinion_not_a_trip():
    """No resolver injected ⇒ the surface is ABSENT, not tripped (fail-open)."""
    harness = _Harness([])
    assert harness.engine.switch_resolver is not None
    bare = EngineService(harness.repo, harness.registry, harness.cfg.engine)
    assert bare.switch_resolver is None
    assert bare._execution_loop._resolve_switch(_spec("x", None)) == (True, None)


# ── MUTATION PROOF 1: per-job trip ⇒ THAT job skips, others proceed ──────

@pytest.mark.asyncio
async def test_per_job_trip_skips_only_that_job_and_restores():
    """D3.5 + D3.6, per-job direction.

    trip  : the job's own ``kill_switch_key`` reads OFF ⇒ THAT job is
            skipped and counted as SKIPPED_KILL_SWITCHED; the OTHER job
            keeps executing (the switch is per-job, not engine-wide).
    restore: flipping the source back ⇒ the job executes again, with NO
            restart of the engine.
    """
    jobs = [
        _spec("job-a", ENGINE_KEY),
        _spec("job-b", OTHER_KEY),
    ]
    h = _Harness(jobs)
    await h.build(jobs)
    h.arm("job-a")
    h.arm("job-b")
    h.source[ENGINE_KEY] = "1"
    h.source[OTHER_KEY] = "1"
    h.source[ENGINE_WIDE_KEY] = "1"

    # ── green: both execute ──
    await h.dispatch("job-a")
    await h.dispatch("job-b")
    assert h.outcomes("job-a") == ["SUCCEEDED"]
    assert h.outcomes("job-b") == ["SUCCEEDED"]
    assert h.calls == {"job-a": 1, "job-b": 1}

    # ── RED: trip the per-job switch of job-a ONLY ──
    h.source[ENGINE_KEY] = "0"
    await h.dispatch("job-a")
    await h.dispatch("job-b")
    assert h.outcomes("job-a") == ["SUCCEEDED", "SKIPPED_KILL_SWITCHED"]
    assert h.outcomes("job-b") == ["SUCCEEDED", "SUCCEEDED"]
    # The skipped job's executor was NOT invoked (the switch really stops it).
    assert h.calls["job-a"] == 1
    assert h.calls["job-b"] == 2
    # Counted + stamped with the canonical D2.6 vocabulary.
    assert h.skip_reasons("job-a")[-1] == "KILL_SWITCHED"
    cache_a = h.engine._cache["job-a"]
    assert cache_a["skips_by_reason"]["KILL_SWITCHED"] == 1
    assert cache_a["last_skip_reason"] == "KILL_SWITCHED"
    assert cache_a["last_outcome"] == "SKIPPED_KILL_SWITCHED"

    # ── restore ⇒ green again (no restart) ──
    h.source[ENGINE_KEY] = "1"
    await h.dispatch("job-a")
    assert h.outcomes("job-a")[-1] == "SUCCEEDED"
    assert h.calls["job-a"] == 2


# ── MUTATION PROOF 2: engine-wide trip ⇒ ALL jobs skip ───────────────────

@pytest.mark.asyncio
async def test_engine_wide_trip_skips_every_job_and_restores():
    """D3.5 + D3.6, engine-wide direction (the mass-rollback lever).

    trip  : ``ENSEMBLE_ENGINE_ENABLED`` reads OFF ⇒ EVERY job is skipped
            and counted as SKIPPED_KILL_SWITCHED (the engine does not own
            execution).
    restore: flipping it back ⇒ every job executes again, no restart.
    """
    jobs = [
        _spec("job-a", ENGINE_KEY),
        _spec("job-b", OTHER_KEY),
    ]
    h = _Harness(jobs)
    await h.build(jobs)
    h.arm("job-a")
    h.arm("job-b")
    h.source[ENGINE_KEY] = "1"
    h.source[OTHER_KEY] = "1"
    h.source[ENGINE_WIDE_KEY] = "1"

    await h.dispatch("job-a")
    await h.dispatch("job-b")
    assert h.calls == {"job-a": 1, "job-b": 1}

    # ── RED: engine-wide trip ──
    h.source[ENGINE_WIDE_KEY] = "0"
    await h.dispatch("job-a")
    await h.dispatch("job-b")
    assert h.outcomes("job-a") == ["SUCCEEDED", "SKIPPED_KILL_SWITCHED"]
    assert h.outcomes("job-b") == ["SUCCEEDED", "SKIPPED_KILL_SWITCHED"]
    # NO executor ran during the trip.
    assert h.calls == {"job-a": 1, "job-b": 1}
    assert h.engine._cache["job-a"]["skips_by_reason"]["KILL_SWITCHED"] == 1
    assert h.engine._cache["job-b"]["skips_by_reason"]["KILL_SWITCHED"] == 1

    # ── restore ⇒ green again (no restart) ──
    h.source[ENGINE_WIDE_KEY] = "1"
    await h.dispatch("job-a")
    await h.dispatch("job-b")
    assert h.outcomes("job-a")[-1] == "SUCCEEDED"
    assert h.outcomes("job-b")[-1] == "SUCCEEDED"
    assert h.calls == {"job-a": 2, "job-b": 2}


# ── MUTATION PROOF 3: the fail-to-legacy polarity (D3.5) ─────────────────

@pytest.mark.asyncio
async def test_unreadable_engine_wide_switch_is_fail_to_legacy():
    """D3.5 — an UNREADABLE engine-wide switch ⇒ the engine does not own
    execution ⇒ every job is skipped (never a stuck/failed engine)."""
    jobs = [_spec("job-a", ENGINE_KEY)]
    h = _Harness(jobs)
    await h.build(jobs)
    h.arm("job-a")
    h.source[ENGINE_KEY] = "1"
    h.source[ENGINE_WIDE_KEY] = "garbage"

    await h.dispatch("job-a")
    assert h.outcomes("job-a") == ["SKIPPED_KILL_SWITCHED"]
    assert h.calls["job-a"] == 0


@pytest.mark.asyncio
async def test_unreadable_per_job_switch_skips_that_job():
    """D3.5 — an UNREADABLE per-job key ⇒ THAT job is skipped and counted."""
    jobs = [_spec("job-a", ENGINE_KEY), _spec("job-b", OTHER_KEY)]
    h = _Harness(jobs)
    await h.build(jobs)
    h.arm("job-a")
    h.arm("job-b")
    h.source[ENGINE_WIDE_KEY] = "1"
    h.source[ENGINE_KEY] = "garbage"
    h.source[OTHER_KEY] = "1"

    await h.dispatch("job-a")
    await h.dispatch("job-b")
    assert h.outcomes("job-a") == ["SKIPPED_KILL_SWITCHED"]
    assert h.outcomes("job-b") == ["SUCCEEDED"]
    assert h.calls == {"job-a": 0, "job-b": 1}


# ── the ladder's third tier: the SHADOW posture ─────────────────────────

@pytest.mark.asyncio
async def test_shadow_tier_is_read_at_tick_time():
    """D3.6 tier 3 — ``ENSEMBLE_ENGINE_SHADOW_MODE`` is read live.

    A shadow flip ON at runtime stops execution on the next dispatch (the
    engine observes, it never invokes the executor) and stamps the
    canonical SKIPPED_SHADOW row.
    """
    jobs = [_spec("job-a", ENGINE_KEY)]
    h = _Harness(jobs, shadow=False)
    await h.build(jobs)
    h.arm("job-a")
    h.source[ENGINE_KEY] = "1"
    h.source[ENGINE_WIDE_KEY] = "1"
    h.source["ENSEMBLE_ENGINE_SHADOW_MODE"] = "false"

    await h.dispatch("job-a")
    assert h.outcomes("job-a") == ["SUCCEEDED"]

    # Runtime flip ON ⇒ the engine observes but never executes.
    h.source["ENSEMBLE_ENGINE_SHADOW_MODE"] = "true"
    await h.dispatch("job-a")
    assert h.outcomes("job-a") == ["SUCCEEDED", "SKIPPED_SHADOW"]
    assert h.calls["job-a"] == 1


@pytest.mark.asyncio
async def test_absent_shadow_key_does_not_override_the_config_tier():
    """D3.6's "(env/config tier)" precedence — ABSENT means "no opinion".

    An adopter whose posture came from the CONFIG tier (shadow_mode=False,
    env var absent) must NOT be pushed back into shadow by the tick-time
    read: an absent key is not an opinion.
    """
    jobs = [_spec("job-a", ENGINE_KEY)]
    h = _Harness(jobs, shadow=False)
    await h.build(jobs)
    h.arm("job-a")
    h.source[ENGINE_KEY] = "1"
    h.source[ENGINE_WIDE_KEY] = "1"
    # NOTE: no ENSEMBLE_ENGINE_SHADOW_MODE in the source ⇒ config tier.

    await h.dispatch("job-a")
    assert h.outcomes("job-a") == ["SUCCEEDED"]
    assert h.calls["job-a"] == 1


# ── end-to-end: the REAL tick loop re-arms with NO restart (D3.6) ───────

@pytest.mark.asyncio
async def test_real_tick_loop_re_arms_on_flip_without_restart():
    """D3.6 end-to-end — a flip takes effect on the NEXT TICK, no restart.

    This drives the REAL loop (``EngineService.start``) so the proof covers
    the wiring the defect was about: the tick path, not a helper.
    """
    jobs = [_spec("job-a", ENGINE_KEY, interval=1.0)]
    h = _Harness(jobs, shadow=False)
    await h.build(jobs)
    h.arm("job-a")
    h.source[ENGINE_KEY] = "1"
    h.source[ENGINE_WIDE_KEY] = "1"

    await h.engine.start()
    try:
        await asyncio.sleep(1.4)  # >= 1 interval ⇒ at least one dispatch
        green = h.calls["job-a"]
        assert green >= 1, "the job never executed — the harness is vacuous"

        # Runtime trip (source mutated, engine NOT restarted).
        h.source[ENGINE_KEY] = "0"
        await asyncio.sleep(1.4)
        assert h.calls["job-a"] == green, "the tripped job still executed"
        assert "SKIPPED_KILL_SWITCHED" in h.outcomes("job-a")

        # Runtime restore (source mutated, engine NOT restarted).
        h.source[ENGINE_KEY] = "1"
        await asyncio.sleep(1.4)
        assert h.calls["job-a"] > green, "the restored job never executed again"
        assert h.outcomes("job-a")[-1] == "SUCCEEDED"
    finally:
        await h.engine.stop()
