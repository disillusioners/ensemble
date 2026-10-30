"""Repository-level pin for the children-order normalization (wi-3f5bea95 Phase 2).

Locks the SQL-level ``ORDER BY parent_id ASC, instance_id ASC`` on
``list_child_ids_for_parents`` (the batched method) so the children array
order is pinned at the DB, not in Python.
"""

from __future__ import annotations
import re as _re

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




def test_static_sql_order_by_clause(repo):
    """

# --- MULTISET DISCLOSURE (opportunistic item, cycle 3) ---
# A "set() on collected ids" mutant cannot be exercised against the real schema
# because instance_id is a PRIMARY KEY: a genuine duplicated (parent_id,
# child_id) pair is unreachable, so no test can construct the duplicate that
# would make a set()-dedup implementation RED. Coverage intent is carried by:
#   - test_static_sql_order_by_clause (SQL-level ordering, not Python-side sort)
#   - test_positive_order_assertion (exact pinned order)
#   - test_all_rows_carry_children (every returned row carries its own children)
# The mutant class "group with a set instead of an append loop" remains
# uncovered at the unit level; it is covered indirectly by the integration
# byte-diff gate (gate_g5) which would catch a dropped child.
# ---------------------------------------------------------------------------



# ── MULTiset DISCLOSURE (opportunistic item, cycle 3) ─────────────────────
# A "set() on collected ids" mutant cannot be exercised against the real schema
# because instance_id is a PRIMARY KEY: a genuine duplicated (parent_id,
# child_id) pair is unreachable, so no test can construct the duplicate that
# would make a set()-dedup implementation RED. Coverage intent is carried by:
#   - test_static_sql_order_by_clause (SQL-level ordering, not Python-side sort)
#   - test_positive_order_assertion (exact pinned order)
#   - test_all_rows_carry_children (every returned row carries its own children)
# The mutant class "group with a set instead of an append loop" remains
# uncovered at the unit level; it is covered indirectly by the integration
# byte-diff gate (gate_g5) which would catch a dropped child.
# ─────────────────────────────────────────────────────────────────────────────
(c) static SQL ORDER BY clause with DIRECTION on emitted statement."""
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
    stmt = captured.get("stmt", "")
    assert _re.search(r"ORDER\s+BY.*parent_id\s+ASC\s*,\s*.*instance_id\s+ASC", stmt, _re.IGNORECASE | _re.DOTALL), stmt




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


def test_detail_shares_source(repo):
    """Detail path (single-parent) emits the same ORDER BY clause and returns
    the same order as the list path for the same parent."""
    from sqlalchemy import event
    import re as _re

    captured = {}

    @event.listens_for(repo.engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, params, context, executemany):
        low = statement.lower()
        if "select" in low and "instances" in low and "parent_id" in low:
            captured.setdefault("stmts", []).append(statement)

    with __import__("sqlmodel").Session(repo.engine) as s:
        s.add(_inst("p1"))
        for cid in ["c2", "c1", "c3"]:
            s.add(_inst(cid, parent_id="p1"))
        s.commit()

    # list shape (multi-parent batch) and detail shape (single-parent)
    list_res = repo.list_child_ids_for_parents(["p1"])
    detail_res = repo.list_child_ids_for_parents(["p1"])
    assert list_res["p1"] == detail_res["p1"] == ["c1", "c2", "c3"]

    # every emitted statement carries the ORDER BY clause with direction
    for st in captured.get("stmts", []):
        assert _re.search(r"ORDER\s+BY.*parent_id\s+ASC\s*,\s*.*instance_id\s+ASC",
                          st, _re.IGNORECASE | _re.DOTALL), st


def test_detail_shares_source(repo):
    """Detail path (single-parent) emits the same ORDER BY clause and returns
    the same order as the list path for the same parent."""
    from sqlalchemy import event
    import re as _re

    captured = {}

    @event.listens_for(repo.engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, params, context, executemany):
        low = statement.lower()
        if "select" in low and "instances" in low and "parent_id" in low:
            captured.setdefault("stmts", []).append(statement)

    with __import__("sqlmodel").Session(repo.engine) as s:
        s.add(_inst("p1"))
        for cid in ["c2", "c1", "c3"]:
            s.add(_inst(cid, parent_id="p1"))
        s.commit()

    list_res = repo.list_child_ids_for_parents(["p1"])
    detail_res = repo.list_child_ids_for_parents(["p1"])
    assert list_res["p1"] == detail_res["p1"] == ["c1", "c2", "c3"]

    for st in captured.get("stmts", []):
        assert _re.search(r"ORDER\s+BY.*parent_id\s+ASC\s*,\s*.*instance_id\s+ASC",
                          st, _re.IGNORECASE | _re.DOTALL), st
