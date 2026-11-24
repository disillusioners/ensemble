"""POK RETRIEVAL-EVENT CAPTURE hook — the ledger's retrieval counts.

Covers pok-write-gate Phase 1 T1.5: the injection path records a
ledger RETRIEVAL event per injected row, and those counts are the
metric that SUPERSEDES ``access_count`` for the librarian because they
DISAMBIGUATE query-vs-injection (design-amendments-v2 §13.2 fork J,
amendment 1).

Fence discipline: throwaway in-memory SQLite engine only. No daemon
boot, no InstanceManager, no live store, no network.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from daemon.repositories.pok.models import (
    POKWriteGateLedger,
    ProjectOperatingKnowledge,
    ProjectOperatingKnowledgeEmbedding,
)
from daemon.repositories.pok.repository import POKRepository
from daemon.services.pok_injection import (
    ViewEntry,
    admit_view,
    make_retrieval_recorder,
)

NOW = datetime(2026, 11, 24, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def repo() -> POKRepository:
    eng = create_engine("sqlite://")
    SQLModel.metadata.create_all(
        eng,
        tables=[
            ProjectOperatingKnowledge.__table__,
            ProjectOperatingKnowledgeEmbedding.__table__,
            POKWriteGateLedger.__table__,
        ],
    )
    return POKRepository(eng)


def _rows() -> list[ProjectOperatingKnowledge]:
    """Two ACTIVE, visible rows (default audience = operation)."""
    out = []
    for i, slug in enumerate(("demo-01-a", "demo-02-b")):
        out.append(
            ProjectOperatingKnowledge(
                project_id="p1",
                slug=slug,
                body=f"body {i}",
                content_sha256=f"{i}" * 64,
                created_by_agent="d",
                audience="both",
            )
        )
    return out


class TestRetrievalCapture:
    def test_retrieval_is_logged(self, repo: POKRepository) -> None:
        """T1.5 acceptance — retrieval logged."""
        recorder = make_retrieval_recorder(repo, "p1", agent_id="worker")
        result = admit_view(_rows(), agent_id="worker", now=NOW, on_retrieval=recorder)
        assert len(result.entries) == 2

        rows = repo.query_ledger("p1", phase="RETRIEVAL")
        assert len(rows) == 2
        assert {r.candidate_slug for r in rows} == {"demo-01-a", "demo-02-b"}
        assert all(r.entry_id for r in rows)
        assert all(r.source == "injection" for r in rows)
        assert all(r.agent_id == "worker" for r in rows)

    def test_the_hook_is_optional(self, repo: POKRepository) -> None:
        """``admit_view`` stays pure when no recorder is supplied."""
        result = admit_view(_rows(), agent_id="worker", now=NOW)
        assert len(result.entries) == 2
        assert repo.query_ledger("p1") == []

    def test_nothing_admitted_logs_nothing(self, repo: POKRepository) -> None:
        recorder = make_retrieval_recorder(repo, "p1")
        admit_view([], agent_id="worker", now=NOW, on_retrieval=recorder)
        assert repo.query_ledger("p1", phase="RETRIEVAL") == []

    def test_a_raising_recorder_never_breaks_the_block(
        self, repo: POKRepository
    ) -> None:
        """§4.9 never-raise: a failed capture costs a metric, not the
        POK block."""

        def _boom(_rows):
            raise RuntimeError("capture exploded")

        result = admit_view(_rows(), agent_id="worker", now=NOW, on_retrieval=_boom)
        assert len(result.entries) == 2
        assert result.degraded is False

    def test_a_failing_append_is_contained(self, repo: POKRepository) -> None:
        """The recorder itself swallows an append failure."""

        class _BrokenRepo:
            def append_ledger(self, _entry):  # noqa: ANN001
                raise RuntimeError("db down")

        entries = [ViewEntry(row=r) for r in _rows()]
        recorder = make_retrieval_recorder(_BrokenRepo(), "p1")
        recorder(entries)  # must not raise

    def test_retrieval_counts_supersede_access_count(
        self, repo: POKRepository
    ) -> None:
        """The librarian's metric: the ledger's retrieval counts are the
        query-vs-injection disambiguator that ``access_count`` is not."""
        rows = _rows()
        recorder = make_retrieval_recorder(repo, "p1", agent_id="worker")
        admit_view(rows, agent_id="worker", now=NOW, on_retrieval=recorder)
        # One injection round.
        assert repo.count_ledger_phase("p1", "RETRIEVAL") == 2
        # A SECOND round — the count is a running total, not a flag.
        admit_view(rows, agent_id="worker", now=NOW, on_retrieval=recorder)
        assert repo.count_ledger_phase("p1", "RETRIEVAL") == 4
        # The source filter separates the injection side from the
        # query side — the whole point of the ledger's retrieval counts.
        assert len(repo.query_ledger("p1", source="injection")) == 4
        assert len(repo.query_ledger("p1", source="query")) == 0

    def test_query_source_is_representable(self, repo: POKRepository) -> None:
        """The read-path side of the same metric — a query retrieval is
        a ledger row with ``source='query'``."""
        repo.append_ledger(
            POKWriteGateLedger(
                project_id="p1",
                phase="RETRIEVAL",
                entry_id="e1",
                candidate_slug="demo-01-a",
                source="query",
                agent_id="worker",
            )
        )
        assert repo.count_ledger_phase("p1", "RETRIEVAL") == 1
        assert len(repo.query_ledger("p1", source="query")) == 1
        assert len(repo.query_ledger("p1", source="injection")) == 0
