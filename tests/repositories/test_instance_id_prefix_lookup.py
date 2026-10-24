"""D3 — the prefix-lookup repository query (``LIKE :prefix || '%'``).

``SQLModelInstanceRepository.find_by_instance_id_prefix`` backs the
agent-tool id resolver's D3 prefix lane. This file pins the QUERY
contract:

* prefix semantics — only ids that START with the prefix match;
* the LIMIT — the resolver needs at most 2 (one hit resolves, two hits
  disambiguate);
* NO status filter — the universe is every instance row, because agents
  legitimately address terminal / just-completed instances (the same
  posture as ``InstanceManager.find_near_instance``);
* deterministic ordering (by id).

Engine: the in-memory SQLite ``engine`` fixture from
``tests/repositories/conftest.py``.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from daemon.repositories.instance.models import Instance, InstanceStatus
from daemon.repositories.instance.repository import SQLModelInstanceRepository


@pytest.fixture
def repo(engine) -> SQLModelInstanceRepository:
    return SQLModelInstanceRepository(engine)


def _seed(engine, instance_id: str, *, status: str = InstanceStatus.RUNNING.value) -> str:
    with Session(engine) as session:
        session.add(
            Instance(
                instance_id=instance_id,
                agent_id="agent-worker",
                agent_name="worker",
                agent_dir="/tmp/worker",
                parent_id=None,
                status=status,
                version=1,
                instance_metadata={},
            )
        )
        session.commit()
    return instance_id


class TestFindByInstanceIdPrefix:
    def test_matches_only_ids_that_start_with_the_prefix(self, engine, repo):
        _seed(engine, "8ade5d22-1111-2222-3333-444455556666")
        _seed(engine, "8ade5d22-9999-8888-7777-666655554444")
        _seed(engine, "0badf00d-1111-2222-3333-444455556666")
        # CONTAINS the prefix but does NOT start with it — a substring
        # match would wrongly return this row.
        _seed(engine, "x8ade5d22-1111-2222-3333-444455556666")

        hits = repo.find_by_instance_id_prefix("8ade5d22", limit=10)
        assert hits == [
            "8ade5d22-1111-2222-3333-444455556666",
            "8ade5d22-9999-8888-7777-666655554444",
        ]

    def test_respects_the_limit(self, engine, repo):
        _seed(engine, "8ade5d22-1111-2222-3333-444455556666")
        _seed(engine, "8ade5d22-9999-8888-7777-666655554444")
        _seed(engine, "8ade5d22-3333-2222-1111-000000000000")

        hits = repo.find_by_instance_id_prefix("8ade5d22", limit=2)
        assert len(hits) == 2
        # Deterministic order — the two smallest ids.
        assert hits == sorted(hits)

    def test_has_no_status_filter(self, engine, repo):
        """Terminal / completed instances stay addressable."""
        _seed(engine, "8ade5d22-1111-2222-3333-444455556666", status=InstanceStatus.COMPLETED.value)
        _seed(engine, "8ade5d22-9999-8888-7777-666655554444", status=InstanceStatus.TERMINATED.value)

        hits = repo.find_by_instance_id_prefix("8ade5d22", limit=10)
        assert len(hits) == 2

    def test_no_match_returns_an_empty_list(self, engine, repo):
        _seed(engine, "0badf00d-1111-2222-3333-444455556666")
        assert repo.find_by_instance_id_prefix("8ade5d22", limit=10) == []

    def test_empty_prefix_returns_an_empty_list(self, engine, repo):
        _seed(engine, "8ade5d22-1111-2222-3333-444455556666")
        assert repo.find_by_instance_id_prefix("", limit=10) == []
