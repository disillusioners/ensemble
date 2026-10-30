"""Repository-level pin for the children-order normalization (wi-3f5bea95 Phase 2).

Locks the SQL-level ``ORDER BY parent_id ASC, instance_id ASC`` on
``list_child_ids_for_parents`` (the batched method) so the children array
order is pinned at the DB, not in Python.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository


@pytest.fixture
def repo():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    return SQLModelInstanceRepository(engine)


def _inst(instance_id, parent_id=None):
    return Instance(instance_id=instance_id, parent_id=parent_id, agent_id="a", agent_dir="/tmp")


def test_sql_order_by_pin(repo):
    # seed parent + children out of order
    repo.engine.connect()
    with __import__("sqlmodel").Session(repo.engine) as s:
        s.add(_inst("p1"))
        for cid in ["c3", "c1", "c2"]:
            s.add(_inst(cid, parent_id="p1"))
        s.commit()
    res = repo.list_child_ids_for_parents(["p1"])
    assert res["p1"] == ["c1", "c2", "c3"]


def test_batch_covers_all_parents(repo):
    with __import__("sqlmodel").Session(repo.engine) as s:
        s.add(_inst("p1"))
        s.add(_inst("p2"))
        for cid in ["a2", "a1"]:
            s.add(_inst(cid, parent_id="p1"))
        s.add(_inst("b1", parent_id="p2"))
        s.commit()
    res = repo.list_child_ids_for_parents(["p1", "p2"])
    assert res["p1"] == ["a1", "a2"]
    assert res["p2"] == ["b1"]


def test_childless_parent_yields_empty_list(repo):
    with __import__("sqlmodel").Session(repo.engine) as s:
        s.add(_inst("p1"))
        s.commit()
    res = repo.list_child_ids_for_parents(["p1"])
    assert res["p1"] == []


def test_empty_input(repo):
    assert repo.list_child_ids_for_parents([]) == {}
    assert repo.list_child_ids_for_parents(None) == {}
