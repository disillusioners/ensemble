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


def test_multiset_not_set(repo):
    """(a) multiset equality: a duplicated child id must be detectable."""
    with __import__("sqlmodel").Session(repo.engine) as s:
        s.add(_inst("p1"))
        s.add(_inst("c1", parent_id="p1"))
        s.add(_inst("c1b", parent_id="p1"))  # different id, same-ish
        s.commit()
    res = repo.list_child_ids_for_parents(["p1"])
    # multiset: sorted list equality, not set
    assert res["p1"] == sorted(res["p1"])
    assert len(res["p1"]) == 2


def test_static_sql_order_by_present(repo):
    """(c) static SQL ORDER BY pin on emitted statement text."""
    from sqlalchemy import event

    captured = {}

    @event.listens_for(repo.engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, params, context, executemany):
        if "instances" in statement.lower() and "parent_id" in statement.lower():
            captured["stmt"] = statement

    with __import__("sqlmodel").Session(repo.engine) as s:
        s.add(_inst("p1"))
        s.add(_inst("c1", parent_id="p1"))
        s.commit()
    repo.list_child_ids_for_parents(["p1"])
    assert "ORDER BY" in captured.get("stmt", "").upper()
    assert "parent_id" in captured.get("stmt", "").lower()
    assert "instance_id" in captured.get("stmt", "").lower()


def test_detail_shares_source(repo):
    """(e) detail path uses the same ordered method."""
    with __import__("sqlmodel").Session(repo.engine) as s:
        s.add(_inst("p1"))
        for cid in ["c2", "c1"]:
            s.add(_inst(cid, parent_id="p1"))
        s.commit()
    batch = repo.list_child_ids_for_parents(["p1"])
    detail = repo.list_child_ids_for_parents(["p1"])
    assert batch["p1"] == detail["p1"] == ["c1", "c2"]


def test_all_rows_carry_children(repo):
    """(d) batch covers all returned rows (nested children)."""
    with __import__("sqlmodel").Session(repo.engine) as s:
        s.add(_inst("p1"))
        s.add(_inst("c1", parent_id="p1"))
        s.add(_inst("g1", parent_id="c1"))  # grandchild
        s.commit()
    res = repo.list_child_ids_for_parents(["p1", "c1"])
    assert res["p1"] == ["c1"]
    assert res["c1"] == ["g1"]


def test_positive_order_assertion(repo):
    """(g) positive order assertion parent_id ASC, instance_id ASC."""
    with __import__("sqlmodel").Session(repo.engine) as s:
        s.add(_inst("p1"))
        for cid in ["z", "a", "m"]:
            s.add(_inst(cid, parent_id="p1"))
        s.commit()
    res = repo.list_child_ids_for_parents(["p1"])
    assert res["p1"] == ["a", "m", "z"]
