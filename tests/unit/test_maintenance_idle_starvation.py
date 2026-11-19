"""Unit pack — the maintenance starvation composite (wi-4d1fa093).

Covers the four parts of the 2026-11-18 composite fix:

* **(A) Per-job idle-policy tiering** — bounded/idempotent jobs run
  UN-GATED; the idle gate is kept only for the two reclaim jobs whose
  contention with live traffic is real.
* **(B) Persisted ``max_staleness_hours`` guarantee + the
  RESTART-CLOCK INVARIANT** — a fresh service instance (empty
  in-memory state) + persisted last-success rows ⇒ the job still
  counts due once past ``max_staleness_hours``, and a restart never
  resets the interval clock.
* **(D) Skip-streak telemetry** — the FIRST skip logs WARNING with the
  consecutive-skip count + the last-success timestamp; later skips log
  DEBUG; one metric block per job.

The (A) behavioural test is the MUTATION TARGET: flip any un-gated
job's policy back to ``IDLE_GATED`` in
:data:`daemon.services.maintenance.JOB_IDLE_POLICIES` and it goes RED.

No live DB contact: the persistence seam is driven with an in-memory
fake, and the one real-store test runs against a local SQLite file.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from sqlmodel import SQLModel, Session, create_engine

from daemon.services.maintenance import (
    DbMaintenanceStateStore,
    IdlePolicy,
    JOB_IDLE_POLICIES,
    JOB_MAX_STALENESS_HOURS,
    MaintenanceService,
    _parse_iso_utc,
    utcnow,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


class FakeStateStore:
    """An in-memory :class:`MaintenanceStateStore` (SYNCHRONOUS methods).

    The seam is deliberately synchronous — the service calls it through
    ``asyncio.to_thread`` — so the fake mirrors that contract exactly
    (no AsyncMock needed, and no accidental await of a sync method).
    """

    def __init__(self, last_success=None, overrides=None):
        self.last_success = dict(last_success or {})
        self.overrides = dict(overrides or {})
        self.saved: list[tuple[str, float | None]] = []

    def load_last_success(self, job_name: str):
        return self.last_success.get(job_name)

    def load_max_staleness_hours(self, job_name: str):
        return self.overrides.get(job_name)

    def save_max_staleness_hours(self, job_name, hours) -> None:
        self.saved.append((job_name, hours))
        if hours is None:
            self.overrides.pop(job_name, None)
        else:
            self.overrides[job_name] = hours


def _busy_service(job_name="skill_orphan_sweep", interval=1.0):
    """A service whose system is BUSY (the idle probe says "not idle")."""
    service = MaintenanceService()
    execute_fn = AsyncMock()
    service.register(job_name, min_interval_hours=interval, execute_fn=execute_fn)
    # A non-empty request registry ⇒ ``_is_idle`` returns False.
    service.set_request_registry({"inflight-request": object()})
    return service, execute_fn


# ─────────────────────────────────────────────────────────────────────
# (A) Per-job idle-policy tiering
# ─────────────────────────────────────────────────────────────────────


class TestIdlePolicyTiering:
    #: The eleven registered maintenance jobs (daemon/manager.py).
    REGISTERED_JOBS = {
        "checkpoint_cleanup",
        "room_gc_sweep",
        "vacuum_maintenance",
        "skill_metric_scan",
        "skill_orphan_sweep",
        "skill_ab_inactivity_sweep",
        "project_skill_gc",
        "blueprint_daily_scan",
        "malloc_trim",
        "dependency_bus_lock_reap",
        "anomaly_retention",
    }

    #: The two jobs whose work genuinely contends with live traffic.
    RECLAIM_JOBS = {"checkpoint_cleanup", "vacuum_maintenance"}

    def test_policy_table_covers_every_registered_job(self):
        assert set(JOB_IDLE_POLICIES) == self.REGISTERED_JOBS

    def test_every_idle_gated_job_carries_a_staleness_guarantee(self):
        """STRUCTURAL GUARD (review cycle 1, item 1).

        Derived from the POLICY TABLE, not from a name literal: every
        IDLE_GATED job must have a non-``None`` ``max_staleness_hours``.
        A future 12th job registered WITHOUT a policy entry silently
        defaults to ``IDLE_GATED`` (the unknown-name default) and, with
        no ``JOB_MAX_STALENESS_HOURS`` entry, would starve forever on a
        busy nest — exactly the defect this composite fixes. This pin
        REDDENS on that registration instead.
        """
        missing = {
            name
            for name, policy in JOB_IDLE_POLICIES.items()
            if policy == IdlePolicy.IDLE_GATED
            and JOB_MAX_STALENESS_HOURS.get(name) is None
        }
        assert missing == set()

    def test_a_gated_job_registered_without_a_policy_entry_is_caught(self):
        """The structural guard also covers the UNKNOWN-NAME default.

        A job name absent from ``JOB_IDLE_POLICIES`` resolves to
        ``IDLE_GATED`` at registration — the guard above only sees the
        table, so this pin covers the registration side: any job the
        service actually holds as IDLE_GATED must carry a guarantee.
        """

        service = MaintenanceService()
        service.register(
            "some_future_reclaim_job", min_interval_hours=1.0,
            execute_fn=AsyncMock(),
        )
        gated_without_guarantee = [
            job.name
            for job in service._jobs
            if job.idle_policy == IdlePolicy.IDLE_GATED
            and job.max_staleness_hours is None
        ]
        # The defect shape IS representable today (a new gated job with
        # no guarantee) — the guard exists so it is SEEN, never silent.
        assert gated_without_guarantee == ["some_future_reclaim_job"]
        # And for the eleven REAL registrations the invariant holds:
        for name, policy in JOB_IDLE_POLICIES.items():
            if policy == IdlePolicy.IDLE_GATED:
                assert JOB_MAX_STALENESS_HOURS.get(name) is not None, name

    def test_reclaim_jobs_keep_the_idle_gate(self):
        for name in self.RECLAIM_JOBS:
            assert JOB_IDLE_POLICIES[name] == IdlePolicy.IDLE_GATED, name

    def test_bounded_jobs_are_un_gated(self):
        un_gated = {
            name for name, policy in JOB_IDLE_POLICIES.items()
            if policy == IdlePolicy.UN_GATED
        }
        assert un_gated == self.REGISTERED_JOBS - self.RECLAIM_JOBS
        assert len(un_gated) == 9

    def test_unknown_job_name_defaults_to_idle_gated(self):
        """An unregistered job never runs unattended (conservative)."""

        service = MaintenanceService()
        service.register(
            "some_future_job", min_interval_hours=1.0, execute_fn=AsyncMock(),
        )
        assert service._jobs[0].idle_policy == IdlePolicy.IDLE_GATED

    def test_explicit_registration_policy_overrides_the_table(self):

        service = MaintenanceService()
        service.register(
            "skill_orphan_sweep",
            min_interval_hours=1.0,
            execute_fn=AsyncMock(),
            idle_policy=IdlePolicy.IDLE_GATED,
        )
        assert service._jobs[0].idle_policy == IdlePolicy.IDLE_GATED

    async def test_un_gated_job_runs_even_when_the_system_is_busy(self):
        """MUTATION TARGET — an un-gated job ignores the idle probe.

        Flip ``JOB_IDLE_POLICIES["skill_orphan_sweep"]`` back to
        ``IdlePolicy.IDLE_GATED`` and this test goes RED.
        """
        service, execute_fn = _busy_service("skill_orphan_sweep")

        await service._run_pending_jobs()

        # The BEHAVIOURAL assertion comes first on purpose: the
        # mutation must red on the guarantee itself, not on a
        # bookkeeping assertion about the policy table.
        assert execute_fn.await_count == 1
        assert service._jobs[0].idle_policy == IdlePolicy.UN_GATED

    async def test_idle_gated_job_does_not_run_when_the_system_is_busy(self):
        service, execute_fn = _busy_service("checkpoint_cleanup")
        assert service._jobs[0].idle_policy == IdlePolicy.IDLE_GATED

        await service._run_pending_jobs()

        assert execute_fn.await_count == 0

    async def test_idle_gated_job_runs_when_the_system_is_idle(self):

        service = MaintenanceService()
        execute_fn = AsyncMock()
        service.register(
            "checkpoint_cleanup", min_interval_hours=1.0, execute_fn=execute_fn,
        )
        service.set_request_registry({})  # idle

        await service._run_pending_jobs()

        assert execute_fn.await_count == 1

    def test_evolution_sweep_is_un_gated_and_the_cas_landed_first(self):
        """The ordering constraint: the sweep may only be un-gated in
        the same change set as the A/B resolve CAS."""
        assert (
            JOB_IDLE_POLICIES["skill_ab_inactivity_sweep"]
            == IdlePolicy.UN_GATED
        )
        from daemon.repositories.skill.repository import SkillABTestRepository
        from daemon.services.skill_evolution_service import (
            SkillEvolutionService,
        )

        assert hasattr(SkillABTestRepository, "resolve_atomic")
        source = inspect.getsource(
            SkillEvolutionService.resolve_ab_test_sanctioned
        )
        assert "resolve_atomic" in source


# ─────────────────────────────────────────────────────────────────────
# (B) Persisted guarantee + the restart-clock invariant
# ─────────────────────────────────────────────────────────────────────


class TestPersistedStalenessGuarantee:
    def test_default_guarantee_only_for_the_two_reclaim_jobs(self):
        assert set(JOB_MAX_STALENESS_HOURS) == {
            "checkpoint_cleanup", "vacuum_maintenance",
        }
        assert JOB_MAX_STALENESS_HOURS["checkpoint_cleanup"] == 24.0
        assert JOB_MAX_STALENESS_HOURS["vacuum_maintenance"] == 24.0

    async def test_restart_clock_invariant_hydrates_the_last_success(self):
        """A fresh service + persisted last-success ⇒ the clock is NOT
        reset (the pre-fix service looked "never run" after a restart)."""
        persisted = utcnow() - timedelta(hours=30)
        store = FakeStateStore(last_success={"checkpoint_cleanup": persisted})
        service = MaintenanceService()          # FRESH — empty in-memory state
        service.set_state_store(store)
        service.register(
            "checkpoint_cleanup", min_interval_hours=24.0,
            execute_fn=AsyncMock(),
        )

        # Pre-hydration: the in-memory clock is empty.
        assert service._jobs[0].last_run is None
        assert service._jobs[0].last_success_ts is None

        await service.hydrate_persisted_state()

        job = service._jobs[0]
        assert job.last_success_ts == persisted
        assert job.last_run == persisted

    async def test_restart_does_not_refire_a_recently_successful_job(self):
        """The invariant, negative side: a job that succeeded 1h ago
        (with a 24h interval) is NOT due after a restart."""
        persisted = utcnow() - timedelta(hours=1)
        store = FakeStateStore(last_success={"checkpoint_cleanup": persisted})
        service = MaintenanceService()
        service.set_state_store(store)
        service.register(
            "checkpoint_cleanup", min_interval_hours=24.0,
            execute_fn=AsyncMock(),
        )

        await service.hydrate_persisted_state()

        assert service._is_due(service._jobs[0]) is False

    async def test_restart_invariant_job_runs_once_past_max_staleness(self):
        """THE RESTART-CLOCK INVARIANT (deliverable 2): a fresh service
        + persisted last-success rows ⇒ the job still counts due once
        past ``max_staleness_hours`` — and it runs even though the
        system is busy."""
        persisted = utcnow() - timedelta(hours=30)   # > 24h
        store = FakeStateStore(last_success={"checkpoint_cleanup": persisted})
        service = MaintenanceService()
        service.set_state_store(store)
        execute_fn = AsyncMock()
        service.register(
            "checkpoint_cleanup", min_interval_hours=24.0,
            execute_fn=execute_fn,
        )
        await service.hydrate_persisted_state()

        job = service._jobs[0]
        assert service._is_due(job) is True
        assert service._is_stale(job) is True

        service.set_request_registry({"inflight-request": object()})  # busy
        await service._run_pending_jobs()

        assert execute_fn.await_count == 1

    async def test_persisted_override_wins_over_the_builtin_default(self):
        store = FakeStateStore(overrides={"checkpoint_cleanup": 6.0})
        service = MaintenanceService()
        service.set_state_store(store)
        service.register(
            "checkpoint_cleanup", min_interval_hours=24.0,
            execute_fn=AsyncMock(),
        )

        await service.hydrate_persisted_state()

        assert service._jobs[0].max_staleness_hours == 6.0

    async def test_zero_override_disables_the_escape_hatch(self):
        """A persisted ``0`` explicitly switches the guarantee off."""
        store = FakeStateStore(overrides={"checkpoint_cleanup": 0.0})
        service = MaintenanceService()
        service.set_state_store(store)
        service.register(
            "checkpoint_cleanup", min_interval_hours=24.0,
            execute_fn=AsyncMock(),
        )
        await service.hydrate_persisted_state()

        job = service._jobs[0]
        assert job.max_staleness_hours == 0.0
        assert service._is_stale(job) is False

    async def test_a_persistently_failing_job_is_not_force_run_every_tick(self):
        """The ATTEMPT clock keeps the escape hatch from turning a
        persistently failing job into an every-tick runner.

        Without it, a job that has never succeeded would be "stale"
        forever and the guarantee would force it on EVERY tick once
        the window is exceeded — even on a busy system, and even
        though it is already attempting as often as it can.
        """
        persisted = utcnow() - timedelta(hours=30)   # never succeeded since
        store = FakeStateStore(last_success={"checkpoint_cleanup": persisted})
        service = MaintenanceService()
        service.set_state_store(store)

        async def _always_fails():
            raise RuntimeError("boom")

        service.register(
            "checkpoint_cleanup", min_interval_hours=24.0,
            execute_fn=_always_fails,
        )
        await service.hydrate_persisted_state()

        job = service._jobs[0]
        # Before the first attempt there is no attempt clock ⇒ the
        # persisted last-success decides ⇒ stale (30h > 24h).
        assert service._is_stale(job) is True
        hydrated_last_run = job.last_run

        service.set_request_registry({"inflight-request": object()})  # busy
        await service._run_pending_jobs()
        # The attempt happened (and failed) ⇒ the attempt clock is now
        # fresh ⇒ NOT stale ⇒ no forced re-run on the next tick.
        assert job.last_attempt_ts is not None
        # The failure did not stamp ``last_run`` (the pre-existing
        # contract: a failed job retries on the next cycle).
        assert job.last_run == hydrated_last_run
        assert service._is_stale(job) is False

    async def test_an_un_gated_job_never_logs_the_busy_override_line(
        self, caplog
    ):
        """ITEM 2 (review cycle 1): the "running despite a busy system"
        WARNING must never fire for an UN_GATED job.

        An un-gated job never waits on the gate, so reporting that it
        ran "despite a busy system" is FALSE. Two independent
        defences are pinned here:

        * ``set_max_staleness_hours`` REFUSES an override on an
          un-gated job (nothing persisted, nothing applied);
        * even if an override attribute survived on an un-gated job
          (e.g. a future re-classification order), the policy
          conjunction in ``_run_pending_jobs`` keeps ``forced`` False.
        """
        service, execute_fn = _busy_service("skill_orphan_sweep")

        # Defence 1: the setter refuses an un-gated target.
        assert (
            await service.set_max_staleness_hours("skill_orphan_sweep", 1.0)
            is False
        )
        assert service._jobs[0].max_staleness_hours is None

        # Defence 2: plant an override + an ancient attempt clock, i.e.
        # the exact shape that used to trip the faulty ``elif forced:``.
        service._jobs[0].max_staleness_hours = 24.0
        service._jobs[0].last_attempt_ts = utcnow() - timedelta(hours=48)

        with caplog.at_level(logging.DEBUG, logger="daemon.services.maintenance"):
            await service._run_pending_jobs()

        assert execute_fn.await_count == 1          # un-gated ⇒ it runs
        busy_lines = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and "despite a busy system" in r.getMessage()
        ]
        assert busy_lines == []

    async def test_a_gated_job_still_logs_the_busy_override_line(
        self, caplog
    ):
        """The truthful counterpart: a genuinely starved IDLE_GATED job
        DOES log the override line (the D-signal stays informative)."""
        service, execute_fn = _busy_service("checkpoint_cleanup")
        # 48h since the last attempt ⇒ past the 24h guarantee.
        service._jobs[0].last_attempt_ts = utcnow() - timedelta(hours=48)
        service._jobs[0].last_success_ts = utcnow() - timedelta(hours=48)

        with caplog.at_level(logging.DEBUG, logger="daemon.services.maintenance"):
            await service._run_pending_jobs()

        assert execute_fn.await_count == 1
        busy_lines = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and "despite a busy system" in r.getMessage()
        ]
        assert len(busy_lines) == 1

    async def test_hydration_ignores_an_override_for_an_un_gated_job(self):
        """A stale persisted override on an un-gated job is ignored at
        hydration (inert by design) — it can never arm the busy line."""
        store = FakeStateStore(overrides={"skill_orphan_sweep": 6.0})
        service = MaintenanceService()
        service.set_state_store(store)
        service.register(
            "skill_orphan_sweep", min_interval_hours=1.0,
            execute_fn=AsyncMock(),
        )

        await service.hydrate_persisted_state()

        assert service._jobs[0].idle_policy == IdlePolicy.UN_GATED
        assert service._jobs[0].max_staleness_hours is None

    async def test_no_store_leaves_the_behaviour_unchanged(self):
        """Without a store the service keeps the pre-fix in-memory
        behaviour (no hydration, no crash)."""
        service = MaintenanceService()
        service.register(
            "checkpoint_cleanup", min_interval_hours=24.0,
            execute_fn=AsyncMock(),
        )
        await service.hydrate_persisted_state()
        assert service._jobs[0].last_run is None
        assert service._jobs[0].last_success_ts is None

    async def test_set_max_staleness_hours_persists_and_applies(self):
        store = FakeStateStore()
        service = MaintenanceService()
        service.set_state_store(store)
        service.register(
            "checkpoint_cleanup", min_interval_hours=24.0,
            execute_fn=AsyncMock(),
        )

        assert await service.set_max_staleness_hours("checkpoint_cleanup", 3.0) is True
        assert store.saved == [("checkpoint_cleanup", 3.0)]
        assert service._jobs[0].max_staleness_hours == 3.0

        assert await service.set_max_staleness_hours("no_such_job", 3.0) is False

    def test_start_hydrates_before_the_first_tick(self):
        """``start()`` hydrates the persisted clock BEFORE the loop runs."""
        persisted = utcnow() - timedelta(hours=30)
        store = FakeStateStore(last_success={"checkpoint_cleanup": persisted})
        service = MaintenanceService()
        service.set_state_store(store)
        service.register(
            "checkpoint_cleanup", min_interval_hours=24.0,
            execute_fn=AsyncMock(),
        )

        async def _drive():
            await service.start()
            await service.stop()

        asyncio.run(_drive())
        assert service._jobs[0].last_run == persisted


class TestDbStateStore:
    """The real store reads the REAL ``maintenance_runs`` schema."""

    def test_load_last_success_reads_the_audit_table(self, tmp_path):
        from daemon.repositories.maintenance_runs.models import MaintenanceRun

        engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
        SQLModel.metadata.create_all(engine)
        completed = datetime(2026, 11, 14, 12, 44, 30, tzinfo=timezone.utc)
        with Session(engine) as session:
            session.add(
                MaintenanceRun(
                    run_id="ckpt-1",
                    section="checkpoint-cleanup",
                    kind="auto",
                    started_at=completed.isoformat(),
                    completed_at=completed.isoformat(),
                    status="succeeded",
                    triggered_by="system",
                )
            )
            # A LATER, FAILED run must not mask the last SUCCESS.
            session.add(
                MaintenanceRun(
                    run_id="ckpt-2",
                    section="checkpoint-cleanup",
                    kind="auto",
                    started_at="2026-11-15T00:00:00+00:00",
                    completed_at="2026-11-15T00:00:00+00:00",
                    status="failed",
                    triggered_by="system",
                )
            )
            session.commit()

        store = DbMaintenanceStateStore(engine=engine)
        loaded = store.load_last_success("checkpoint_cleanup")

        assert loaded is not None
        assert loaded == completed
        # A job with no audit trail hydrates to None.
        assert store.load_last_success("malloc_trim") is None
        engine.dispose()

    @staticmethod
    def _seed_runs(tmp_path, rows):
        """Create a fresh SQLite ``maintenance_runs`` table + seed rows.

        ``rows`` = list of ``(run_id, section, kind, completed_at,
        status)`` tuples.
        """
        from daemon.repositories.maintenance_runs.models import MaintenanceRun

        engine = create_engine(f"sqlite:///{tmp_path / 'kind.db'}")
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            for run_id, section, kind, completed_at, status in rows:
                session.add(
                    MaintenanceRun(
                        run_id=run_id,
                        section=section,
                        kind=kind,
                        started_at=completed_at,
                        completed_at=completed_at,
                        status=status,
                        triggered_by=(
                            "system" if kind == "auto" else "user"
                        ),
                    )
                )
            session.commit()
        return engine

    def test_load_last_success_ignores_a_newer_manual_dry_run_row(
        self, tmp_path
    ):
        """AM-9: ``manual_dry_run`` NEVER surfaces.

        A dry run mutates nothing, so a NEWER dry-run ``succeeded``
        row must not hydrate as last-success — otherwise it would push
        the real last-success forward and delay the next auto reclaim
        by up to ``max_staleness_hours``.
        """
        auto_completed = "2026-11-14T12:44:30+00:00"
        dry_run_completed = "2026-11-18T09:00:00+00:00"   # NEWER
        engine = self._seed_runs(
            tmp_path,
            [
                ("auto-1", "checkpoint-cleanup", "auto",
                 auto_completed, "succeeded"),
                ("dry-1", "checkpoint-cleanup", "manual_dry_run",
                 dry_run_completed, "succeeded"),
            ],
        )
        store = DbMaintenanceStateStore(engine=engine)

        loaded = store.load_last_success("checkpoint_cleanup")

        assert loaded == _parse_iso_utc(auto_completed)
        assert loaded != _parse_iso_utc(dry_run_completed)
        engine.dispose()

    def test_load_last_success_counts_a_manual_execute_row(self, tmp_path):
        """A REAL manual execute is a genuine run — it counts alongside
        ``auto`` (the repository convention's ``{auto, manual_execute}``
        kind set)."""
        auto_completed = "2026-11-14T12:44:30+00:00"
        manual_completed = "2026-11-18T09:00:00+00:00"     # NEWER
        engine = self._seed_runs(
            tmp_path,
            [
                ("auto-1", "checkpoint-cleanup", "auto",
                 auto_completed, "succeeded"),
                ("exec-1", "checkpoint-cleanup", "manual_execute",
                 manual_completed, "succeeded"),
            ],
        )
        store = DbMaintenanceStateStore(engine=engine)

        loaded = store.load_last_success("checkpoint_cleanup")

        assert loaded == _parse_iso_utc(manual_completed)
        engine.dispose()

    def test_a_dry_run_only_section_has_no_last_success(self, tmp_path):
        """A section whose ONLY ``succeeded`` row is a dry run hydrates
        to ``None`` (no real run has ever completed)."""
        engine = self._seed_runs(
            tmp_path,
            [
                ("dry-1", "checkpoint-cleanup", "manual_dry_run",
                 "2026-11-18T09:00:00+00:00", "succeeded"),
            ],
        )
        store = DbMaintenanceStateStore(engine=engine)

        assert store.load_last_success("checkpoint_cleanup") is None
        engine.dispose()

    async def test_set_max_staleness_hours_is_false_when_the_write_noops(self):
        """An override that was NOT persisted must not be reported as
        persisted (rider #2 — the honest return)."""

        class _AbsentProjectRepo:
            """``set_metadata`` returns None — the system project row is
            absent, so the write is a silent no-op."""

            def get_metadata(self, project_id, key):
                return None

            def set_metadata(self, project_id, key, value):
                return None

        store = DbMaintenanceStateStore(
            engine=None,
            project_repository=_AbsentProjectRepo(),
            system_project_id="missing-project",
        )
        service = MaintenanceService()
        service.set_state_store(store)
        service.register(
            "checkpoint_cleanup", min_interval_hours=24.0,
            execute_fn=AsyncMock(),
        )

        assert (
            await service.set_max_staleness_hours("checkpoint_cleanup", 3.0)
            is False
        )
        # The in-memory value was NOT applied either — no lie anywhere.
        assert service._jobs[0].max_staleness_hours == 24.0

    async def test_set_max_staleness_hours_is_false_without_a_repository(self):
        """No project repository wired ⇒ the override cannot persist,
        so the call reports ``False`` instead of pretending."""
        store = DbMaintenanceStateStore(engine=None, project_repository=None)
        service = MaintenanceService()
        service.set_state_store(store)
        service.register(
            "checkpoint_cleanup", min_interval_hours=24.0,
            execute_fn=AsyncMock(),
        )

        assert (
            await service.set_max_staleness_hours("checkpoint_cleanup", 3.0)
            is False
        )
        assert service._jobs[0].max_staleness_hours == 24.0

    def test_kv_override_round_trip(self):
        class _FakeProjectRepo:
            def __init__(self):
                self.blobs = {}

            def get_metadata(self, project_id, key):
                return self.blobs.get((project_id, key))

            def set_metadata(self, project_id, key, value):
                self.blobs[(project_id, key)] = value
                return object()

        repo = _FakeProjectRepo()
        store = DbMaintenanceStateStore(
            engine=None, project_repository=repo,
            system_project_id="pid-1",
        )

        assert store.load_max_staleness_hours("checkpoint_cleanup") is None

        store.save_max_staleness_hours("checkpoint_cleanup", 12.0)
        assert store.load_max_staleness_hours("checkpoint_cleanup") == 12.0

        store.save_max_staleness_hours("checkpoint_cleanup", None)
        assert store.load_max_staleness_hours("checkpoint_cleanup") is None


# ─────────────────────────────────────────────────────────────────────
# (D) Skip-streak telemetry
# ─────────────────────────────────────────────────────────────────────


class TestSkipStreakTelemetry:
    async def test_first_skip_logs_warning_with_streak_and_last_success(
        self, caplog
    ):
        service, execute_fn = _busy_service("checkpoint_cleanup")
        service._jobs[0].last_success_ts = utcnow() - timedelta(hours=2)

        with caplog.at_level(logging.DEBUG, logger="daemon.services.maintenance"):
            await service._run_pending_jobs()

        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and "checkpoint_cleanup" in r.getMessage()
        ]
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "consecutive_skips=1" in message
        assert "last_success_ts=" in message
        assert execute_fn.await_count == 0

    async def test_subsequent_skips_log_debug_only(self, caplog):
        service, _ = _busy_service("checkpoint_cleanup")

        with caplog.at_level(logging.DEBUG, logger="daemon.services.maintenance"):
            await service._run_pending_jobs()
            await service._run_pending_jobs()
            await service._run_pending_jobs()

        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING
            and "checkpoint_cleanup" in r.getMessage()
        ]
        # Require "skipped": the job name alone also matches ambient lifecycle
        # records (e.g. "Registered maintenance job: checkpoint_cleanup ..."),
        # which surface only when daemon.services.maintenance sits at DEBUG.
        debugs = [
            r for r in caplog.records
            if r.levelno == logging.DEBUG
            and "checkpoint_cleanup" in r.getMessage()
            and "skipped" in r.getMessage()
        ]
        assert len(warnings) == 1
        assert len(debugs) == 2
        assert "consecutive_skips=3" in debugs[-1].getMessage()

    async def test_success_resets_the_skip_streak(self):
        service, execute_fn = _busy_service("checkpoint_cleanup")

        await service._run_pending_jobs()   # skipped — the system is busy
        assert execute_fn.await_count == 0
        assert service._jobs[0].consecutive_skips == 1

        service.set_request_registry({})   # now idle
        await service._run_pending_jobs()   # runs
        assert execute_fn.await_count == 1
        assert service._jobs[0].consecutive_skips == 0

    async def test_job_metrics_exposes_one_block_per_job(self):
        service, _ = _busy_service("checkpoint_cleanup")
        service._jobs[0].last_success_ts = utcnow() - timedelta(hours=2)
        await service._run_pending_jobs()

        metrics = service.job_metrics()
        assert set(metrics) == {"checkpoint_cleanup"}
        block = metrics["checkpoint_cleanup"]
        assert set(block) == {"last_success_ts", "consecutive_skips"}
        assert block["consecutive_skips"] == 1
        assert block["last_success_ts"] is not None
