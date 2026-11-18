"""Item (e) — e2e capture-ADMISSION proof through the REAL enqueue path.

WHY THIS TEST EXISTS (the dark capture lane, 2026-11-17): the
production capture trigger chain
``record_task_completion`` -> ``_check_capture_eligibility`` ->
``check_and_capture`` -> ``SkillJobDispatcher.enqueue_capture`` had NO
end-to-end proof — the existing integration tests MOCK the dispatcher,
and the manual ``skill_execute_capture`` tool BYPASSES
``enqueue_capture`` entirely (inline LLM + insert, no admission
record), so it cannot serve as the proof seam. Live forensics (499
qualifying candidates, zero ``skill_capture`` jobs since 2026-09-01)
could not distinguish "the chain is broken" from "the lane is off".

This test drives the REAL chain against the DEV PostgreSQL with the
gate ON — every repository is real (skill repos, queue repo, job
repo, lock repo); only the instance-row seam is stubbed (synthetic
injected-skills metadata — the instances table is not this lane's to
seed). The assertion is the durable proof: a ``skill_capture``
JobItem row LANDS in the dev DB with ``capture_run_id`` in its
metadata, through ``system_parallel_queue`` routing.

DB FENCE: dev database ONLY (``ensemble_dev`` @ 127.0.0.1:5432 per
the worktree fence). Hard-skip on any other resolved DB name. The
test seeds a synthetic project + queue + one job row and removes them
in teardown (best-effort) — no live-shaped data is touched.
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.integration


def _dev_engine():
    """Build a real engine against the FENCED dev database, or skip."""
    db = os.environ.get("POSTGRES_DB", "ensemble_dev")
    if db != "ensemble_dev":
        pytest.skip("POSTGRES_DB is not the dev database — refusing")
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "5432")
    user = os.environ.get("POSTGRES_USER", "postgres")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    auth = f"{user}:{password}@" if password else f"{user}@"
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(
            f"postgresql+psycopg://{auth}{host}:{port}/{db}", future=True
        )
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception:
        pytest.skip("dev PostgreSQL unreachable — e2e pin is opt-in")


@pytest.fixture
def e2e_env(monkeypatch, tmp_path):
    """Gate ON (the env flip the operator makes on live is simulated
    per-test; no switch file is touched) + sink sandboxed to tmp."""
    from daemon.services import skill_capture_observability as obs
    from tests.helpers.skill_sink_redirect import (
        lane_test_end,
        lane_test_start,
        restore_safe_sink_redirect,
        snapshot_real_sink_dir,
    )

    monkeypatch.setenv("ENSEMBLE_SKILL_CAPTURE_ENABLED", "1")
    monkeypatch.delenv("ENSEMBLE_SKILL_EVOLUTION_ENABLED", raising=False)
    node_id = "capture-admission-e2e"
    lane_test_start(node_id)
    before = snapshot_real_sink_dir()
    obs.configure_sink(tmp_path)
    obs.reset_counters()
    try:
        yield tmp_path
    finally:
        restore_safe_sink_redirect()
        obs.reset_counters()
        lane_test_end(node_id, before)


def test_capture_admission_e2e_through_real_enqueue(e2e_env):
    engine = _dev_engine()

    from daemon.repositories.job_queue.lock_repository import LockRepository
    from daemon.repositories.job_queue.queue_repository import (
        JobQueueRepository,
    )
    from daemon.repositories.job_queue.repository import JobRepository
    from daemon.repositories.skill import (
        SkillABTestRepository,
        SkillEmbeddingRepository,
        SkillLineageRepository,
        SkillRepository,
        SkillTriggerRepository,
        SkillUsageRepository,
    )
    from daemon.services.job_lock_manager import JobLockManager
    from daemon.services.job_queue_service import JobQueueService
    from daemon.services.skill_evolution_service import SkillEvolutionService
    from daemon.services.skill_job_dispatcher import SkillJobDispatcher
    from daemon.services.skill_metrics_service import (
        INJECTED_SKILLS_METADATA_KEY,
        SkillMetricsService,
    )
    from daemon.config import SkillEvolutionConfig

    project_id = f"e2e-capture-{uuid.uuid4().hex[:10]}"
    instance_id = f"e2e-inst-{uuid.uuid4().hex[:8]}"

    skill_repo = SkillRepository(engine)
    lineage_repo = SkillLineageRepository(engine)
    usage_repo = SkillUsageRepository(engine)
    ab_test_repo = SkillABTestRepository(engine)
    queue_repo = JobQueueRepository(engine)
    job_repo = JobRepository(engine)
    lock_repo = LockRepository(engine)

    # Seed: one skill (the "injected" one) + the project's parallel
    # queue. Both are removed in teardown.
    skill = skill_repo.create(
        name=f"e2e-capture-probe-{uuid.uuid4().hex[:6]}",
        description="e2e capture admission probe (dev-only)",
        content="# Probe\n\ne2e admission proof",
        project_id=project_id,
    )
    queue = queue_repo.create(
        project_id=project_id,
        queue_name="system_parallel_queue",
        queue_type="parallel",
        concurrency_limit=5,
        is_system=True,
    )

    job_service = JobQueueService(
        repository=job_repo,
        lock_manager=JobLockManager(lock_repo),
        queue_repo=queue_repo,
    )
    dispatcher = SkillJobDispatcher(
        job_service=job_service,
        queue_repo=queue_repo,
    )

    embedding_service = MagicMock()
    evolution_service = SkillEvolutionService(
        skill_repo=skill_repo,
        lineage_repo=lineage_repo,
        usage_repo=usage_repo,
        embedding_service=embedding_service,
        metrics_service=MagicMock(),
        ab_test_repo=ab_test_repo,
        config=SkillEvolutionConfig(),
        llm_config={"base_url": "http://test", "api_key": "test"},
    )

    # Instance-row seam: synthetic metadata claiming the probe skill
    # was injected (the instances table is not this lane's to seed).
    instance_repo = SimpleNamespace(
        get=lambda iid: SimpleNamespace(
            instance_id=iid,
            instance_metadata={INJECTED_SKILLS_METADATA_KEY: [skill.id]},
        ),
        delete_metadata=lambda iid, key: None,
        set_metadata=lambda iid, key, value: None,
    )

    metrics_service = SkillMetricsService(
        usage_repo=usage_repo,
        skill_repo=skill_repo,
        trigger_repo=SkillTriggerRepository(engine),
        ab_test_repo=ab_test_repo,
        config=SkillEvolutionConfig(),
        instance_repo=instance_repo,
        evolution_service=evolution_service,
        agent_id_resolver=lambda aid, instance_id=None: SimpleNamespace(
            skill_injection=True
        ),
    )
    metrics_service.set_job_dispatcher(dispatcher)

    try:
        inserted = asyncio_run(
            metrics_service.record_task_completion(
                instance_id=instance_id,
                agent_id="worker",
                project_id=project_id,
                task_succeeded=True,
                iterations=10,
                duration_seconds=120,
                task_message="e2e: a complex multi-step task that deserves capture",
            )
        )
        assert inserted >= 1, "usage rows must be recorded"

        # The durable proof: the skill_capture JobItem landed on the
        # dev DB through the REAL enqueue path (parallel queue
        # routing + capture_run_id metadata + admission sink record).
        from sqlmodel import Session, select
        from daemon.repositories.job_queue.models import JobItem

        with Session(engine) as session:
            stmt = select(JobItem).where(
                JobItem.job_type == "skill_capture",
                JobItem.project_id == project_id,
            )
            jobs = list(session.exec(stmt))
        assert len(jobs) == 1, (
            f"expected exactly one skill_capture job, got {len(jobs)}"
        )
        captured = jobs[0]
        assert captured.queue_id == queue.queue_id, (
            "the capture job MUST route through system_parallel_queue"
        )
        meta = captured.job_metadata or {}
        assert meta.get("capture_run_id"), (
            "the admission record's run id must ride the job metadata"
        )
        assert meta.get("task_details", {}).get("instance_id") == instance_id

        # Sink: the admission record exists and carries the SAME run
        # id (C3 correlation) + the admitted counter moved.
        from daemon.services import skill_capture_observability as obs
        from pathlib import Path

        sink_files = list(
            (e2e_env / obs.SINK_DIR_NAME).glob("*.jsonl")
        )
        assert sink_files, "admission sink record missing"
        import json as _json

        records = [
            _json.loads(line)
            for path in sink_files
            for line in path.read_text().strip().splitlines()
            if line
        ]
        admitted = [
            r
            for r in records
            if r.get("stage") == "admission"
            and r.get("outcome") == "admitted"
        ]
        assert admitted, "the ADMITTED record must be in the sink"
        assert admitted[-1].get("run_id") == meta.get("capture_run_id"), (
            "sink run id and job metadata run id must correlate"
        )
    finally:
        # Teardown (best-effort): remove the seeded rows.
        from sqlmodel import Session, select
        from daemon.repositories.job_queue.models import JobItem
        from daemon.repositories.skill.models import (
            SkillUsageRecord,
        )

        try:
            with Session(engine) as session:
                for job in session.exec(
                    select(JobItem).where(JobItem.project_id == project_id)
                ):
                    session.delete(job)
                session.commit()
            queue_repo.delete(queue.queue_id)
            with Session(engine) as session:
                for row in session.exec(
                    select(SkillUsageRecord).where(
                        SkillUsageRecord.instance_id == instance_id
                    )
                ):
                    session.delete(row)
                session.commit()
            skill_repo.delete(skill.id)
        except Exception:
            pass
        engine.dispose()


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)
