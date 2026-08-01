"""P1/F11 shared predicate invariant."""

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from daemon.repositories.task.repository import TaskRepository


@pytest.fixture
def engine():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_p1_f11_predicates_agree(engine):
    """The two call sites' predicates differ only in the job alias."""
    repo = TaskRepository(engine)
    claim = repo._active_jobitem_with_inflight_task_sql("j")
    busy = repo._active_jobitem_with_inflight_task_sql("j_running")
    claim_normalized = claim.replace("j.job_id", "ALIAS.job_id")
    busy_normalized = busy.replace("j_running.job_id", "ALIAS.job_id")
    assert claim_normalized == busy_normalized
