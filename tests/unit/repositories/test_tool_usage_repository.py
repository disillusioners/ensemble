"""Unit tests for the ``tool_usage_daily`` repository (Phase 1).

Pins (architect §10.5 mutation-pinned split):
* NAMES-ONLY schema — no args/argv/content/tool_call_id/instance/session
  column can be added without a pin trip.
* Upsert aggregation + PK conflict semantics.
* A-6 dual-dialect coalescing — two same-PK deltas in ONE batch → one
  row summed (PG cardinality_violation guard; the map satisfies it by
  construction and the repository coalesces as a second guarantee).
* Sentinel round-trips (agent='unknown', project='__none__').
* Empty-batch no-op.
* Door read shapes + top-50 agents cap + project filter semantics.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlmodel import SQLModel

from daemon.repositories.tool_usage.models import (
    NONE_PROJECT,
    UNKNOWN_AGENT,
    ToolUsageDaily,
)
from daemon.repositories.tool_usage.repository import (
    MAX_AGENTS_PER_TOOL,
    ToolUsageDelta,
    ToolUsageRepository,
)

TODAY = "2026-11-21"


@pytest.fixture()
def repo():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    return ToolUsageRepository(engine)


def _delta(day=TODAY, project="p1", tool="bash", agent="coder", n=1, ts=None):
    return ToolUsageDelta(
        day=day,
        project_id=project,
        tool_name=tool,
        agent_id=agent,
        call_count=n,
        updated_at=ts or "2026-11-21T08:00:00+00:00",
    )


def _rows(repo):
    with repo._engine.connect() as conn:
        return conn.execute(select(ToolUsageDaily.__table__)).all()


class TestNamesOnlySchema:
    def test_no_argument_or_identity_columns(self):
        cols = set(ToolUsageDaily.__table__.columns.keys())
        forbidden = {
            "args", "argv", "content", "tool_call_id", "instance_id",
            "session_id", "arguments", "input",
        }
        assert not (cols & forbidden), f"NAMES-ONLY violated: {cols & forbidden}"
        assert cols == {
            "day", "project_id", "tool_name", "agent_id", "call_count",
            "updated_at",
        }

    def test_index_names_lockstep(self):
        idx = {i.name: tuple(c.name for c in i.columns)
               for i in ToolUsageDaily.__table__.indexes}
        assert idx["idx_tool_usage_daily_project_day"] == ("project_id", "day")
        assert idx["idx_tool_usage_daily_tool_day"] == ("tool_name", "day")


class TestUpsert:
    def test_empty_batch_noop(self, repo):
        assert repo.upsert_batch([]) == 0
        assert _rows(repo) == []

    def test_two_increments_same_pk_sum(self, repo):
        repo.upsert_batch([_delta(n=3)])
        repo.upsert_batch([_delta(n=4)])
        rows = _rows(repo)
        assert len(rows) == 1
        assert rows[0].call_count == 7

    def test_a6_two_same_pk_deltas_in_one_batch_coalesced(self, repo):
        """A-6: two deltas with the SAME PK in ONE batch → one row, summed.

        PG would raise cardinality_violation on a duplicate PK in a
        multi-row VALUES; the repository coalesces before the VALUES build
        so both dialects see exactly one row per PK.
        """
        repo.upsert_batch([_delta(n=2, ts="2026-11-21T08:00:00+00:00"),
                           _delta(n=5, ts="2026-11-21T09:00:00+00:00")])
        rows = _rows(repo)
        assert len(rows) == 1, "duplicate PK in one batch must coalesce"
        assert rows[0].call_count == 7
        assert rows[0].updated_at == "2026-11-21T09:00:00+00:00"  # latest

    def test_distinct_pks_separate_rows(self, repo):
        repo.upsert_batch([
            _delta(tool="bash", n=1),
            _delta(tool="read_file", n=2),
            _delta(tool="bash", agent="tester", n=3),
        ])
        assert len(_rows(repo)) == 3

    def test_sentinel_agent_roundtrips(self, repo):
        repo.upsert_batch([_delta(agent=UNKNOWN_AGENT)])
        assert _rows(repo)[0].agent_id == UNKNOWN_AGENT

    def test_sentinel_project_roundtrips(self, repo):
        repo.upsert_batch([_delta(project=NONE_PROJECT)])
        assert _rows(repo)[0].project_id == NONE_PROJECT

    def test_updated_at_uses_excluded_flush_time(self, repo):
        repo.upsert_batch([_delta(n=1, ts="2026-11-21T08:00:00+00:00")])
        repo.upsert_batch([_delta(n=1, ts="2026-11-21T10:00:00+00:00")])
        assert _rows(repo)[0].updated_at == "2026-11-21T10:00:00+00:00"

    def test_pg_dialect_path_uses_same_coalesced_rows(self, repo):
        """The PG branch selects pg_insert but shares the coalescing.

        A fake PG-flavoured connection captures the statement: the VALUES
        clause must carry exactly ONE row with the summed count (the
        cardinality_violation precondition), proving the guarantee is
        dialect-independent without needing a live PG.
        """
        from unittest.mock import MagicMock

        captured = {}

        class _FakeConn:
            dialect = MagicMock(name="dialect")
            dialect.name = "postgresql"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, stmt):
                captured["stmt"] = stmt
                result = MagicMock()
                result.rowcount = 1
                return result

        fake_engine = MagicMock()
        fake_engine.begin.return_value = _FakeConn()
        pg_repo = ToolUsageRepository(fake_engine)
        pg_repo.upsert_batch([_delta(n=2), _delta(n=5)])
        stmt = captured["stmt"]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        # One VALUES tuple (one 'p1'/'bash' payload group) with the summed
        # count 7 — the cardinality_violation precondition, both dialects.
        assert "'p1', 'bash', 'coder', 7" in compiled, compiled
        assert compiled.count("'p1', 'bash', 'coder'") == 1, compiled


class TestDoorReads:
    def test_query_summary_windows_and_agents(self, repo):
        repo.upsert_batch([
            _delta(day=TODAY, tool="bash", agent="coder", n=3),
            _delta(day=TODAY, tool="bash", agent="tester", n=2),
            _delta(day="2026-11-15", tool="bash", agent="coder", n=4),
            _delta(day="2026-11-01", tool="bash", agent="coder", n=10),
        ])
        rows = repo.query_summary(None, 7, today=TODAY)
        assert len(rows) == 1
        row = rows[0]
        assert row["tool_name"] == "bash"
        assert row["calls_24h"] == 5
        assert row["calls_7d"] == 9           # today 5 + 11-15 4
        assert row["calls_total"] == 19       # + 11-01 10 (all days)
        assert {a["agent_id"] for a in row["agents"]} == {"coder", "tester"}

    def test_query_summary_project_filter_includes_sentinel_when_omitted(self, repo):
        repo.upsert_batch([
            _delta(project="p1", tool="bash", n=1),
            _delta(project=NONE_PROJECT, tool="bash", n=2),
        ])
        all_rows = repo.query_summary(None, 7, today=TODAY)
        assert all_rows[0]["calls_7d"] == 3  # total truth incl __none__
        p1_rows = repo.query_summary("p1", 7, today=TODAY)
        assert p1_rows[0]["calls_7d"] == 1
        none_rows = repo.query_summary(NONE_PROJECT, 7, today=TODAY)
        assert none_rows[0]["calls_7d"] == 2

    def test_top_50_agents_cap(self, repo):
        deltas = [
            _delta(tool="bash", agent=f"agent-{i:03d}", n=i + 1)
            for i in range(60)
        ]
        repo.upsert_batch(deltas)
        row = repo.query_summary(None, 7, today=TODAY)[0]
        assert len(row["agents"]) == MAX_AGENTS_PER_TOOL == 50
        # top agents first (highest call_count)
        assert row["agents"][0]["calls_7d"] == 60

    def test_query_daily_zero_filled(self, repo):
        repo.upsert_batch([
            _delta(day=TODAY, tool="bash", n=3),
            _delta(day="2026-11-19", tool="bash", n=2),
        ])
        series = repo.query_daily(None, "bash", 3, today=TODAY)
        assert [p["day"] for p in series] == [
            "2026-11-19", "2026-11-20", "2026-11-21"
        ]
        assert [p["calls"] for p in series] == [2, 0, 3]

    def test_has_tool(self, repo):
        assert repo.has_tool("bash") is False
        repo.upsert_batch([_delta(tool="bash")])
        assert repo.has_tool("bash") is True
        assert repo.has_tool("other") is False
