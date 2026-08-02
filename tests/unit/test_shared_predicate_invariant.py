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
    """The two call sites' predicates differ only in the job alias and
    the exclude-task alias (self-deadlock fix, 2026-08-02).

    Both call sites must normalize to the same shape when their distinct
    aliases are replaced with a common placeholder — this pins the P1/F11
    invariant that they share a single source of truth.
    """
    repo = TaskRepository(engine)
    claim = repo._active_jobitem_with_inflight_task_sql("j", exclude_task_alias="task")
    busy = repo._active_jobitem_with_inflight_task_sql("j_running", exclude_task_alias="t_pending")
    # Normalize job_alias and exclude_task_alias to a common placeholder so
    # the only allowed differences are whitespace (the helper appends a
    # clause line only when exclude_task_alias is provided — both call
    # sites now provide it, so both have the same number of lines).
    claim_normalized = (
        claim.replace("j.job_id", "ALIAS.job_id").replace("task.id", "EX.id")
    )
    busy_normalized = (
        busy.replace("j_running.job_id", "ALIAS.job_id").replace("t_pending.id", "EX.id")
    )
    assert claim_normalized == busy_normalized
