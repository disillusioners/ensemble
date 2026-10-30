"""Route-level pin: GET /api/jobs with and without ?limit= (wi-3f5bea95 C1)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401

from daemon.repositories.job_queue.lock_repository import LockRepository
from daemon.repositories.job_queue.models import AdmissionState, JobItem
from daemon.repositories.job_queue.queue_repository import JobQueueRepository
from daemon.repositories.job_queue.repository import JobRepository
from daemon.routers.jobs_crud import (
    get_dead_letter_svc,
    get_job_queue_service,
    router as jobs_crud_router,
)
from daemon.services.dead_letter_service import DeadLetterService
from daemon.services.job_lock_manager import JobLockManager
from daemon.services.job_queue_service import JobQueueService


@pytest.fixture
def engine(tmp_path):
    db_path = tmp_path / "jobs-limit.sqlite"
    eng = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False}, poolclass=NullPool)
    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def job_queue_service(engine):
    job_repo = JobRepository(engine)
    lock_repo = LockRepository(engine)
    queue_repo = JobQueueRepository(engine)
    lock_manager = JobLockManager(lock_repo=lock_repo)
    return JobQueueService(job_repo, lock_manager, queue_repo)


@pytest.fixture
def client(job_queue_service):
    app = FastAPI()
    app.include_router(jobs_crud_router, prefix="/api")
    app.dependency_overrides[get_job_queue_service] = lambda: job_queue_service
    app.dependency_overrides[get_dead_letter_svc] = lambda: DeadLetterService.__new__(DeadLetterService)
    return TestClient(app)


def test_list_jobs_without_limit(client):
    resp = client.get("/api/jobs")
    assert resp.status_code == 200, resp.text


def test_list_jobs_with_limit(client):
    resp = client.get("/api/jobs?limit=10")
    assert resp.status_code == 200, resp.text
