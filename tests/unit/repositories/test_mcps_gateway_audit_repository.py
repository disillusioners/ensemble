"""Repository tests for the PRODUCTION audit read path (Phase C).

``McpGatewayAuditRepository.list_recent(limit, offset)`` is the code the
operator page's audit tail actually runs on — the DB-backed
``AuditReader`` behind ``McpGatewayService.audit_tail``
(``daemon/services/mcps_gateway/service.py:758``).

Until this module existed, NO test executed that query: the endpoint
tests (``tests/unit/routers/test_mcps_gateway_read.py``) drive the read
surface against ``MemoryAuditSink`` — the in-memory FAKE. A broken SQL
(a wrong ORDER BY, a broken ``.offset()`` clause, a column that does not
exist) would therefore have reached the frontend as a SILENTLY EMPTY
audit list with HTTP 200. This module closes that gap.

THE HARNESS
-----------

In-memory SQLite with a ``StaticPool`` — the dominant pattern of this
directory (``tests/unit/repositories/test_message_metadata_repository.py``
and ``tests/unit/repositories/test_job_queue_atomic_transition.py``):
the connection is shared across the test, so there is no daemon, no
port and NO live PostgreSQL (there is none in this environment).

The ``mcp_gateway_audit`` table is dialect-neutral: EVERY column is a
``Text`` (``daemon/repositories/mcps_gateway/models.py``), so the same
DDL and the same ``ORDER BY created_at DESC`` run on SQLite and on
PostgreSQL. ``created_at`` is an ISO-8601 TEXT stamp — NOT a timestamp
column — so the repo-wide ``now_utc_naive()`` bind rule does NOT apply
to this table and there is no tz-aware/naive bind to get wrong here.

WHAT IS PINNED
--------------

* the ORDER is NEWEST FIRST (``created_at DESC``), over rows inserted
  in a deliberately UNSORTED order — a read that leaked the insertion
  order cannot pass;
* ``limit`` caps the page and ``offset`` skips rows from the HEAD of
  the newest-first list (the pagination seam of §8.4), with no overlap
  and no gap between the pages;
* an ``offset`` past the end is an EMPTY LIST, not an error;
* the row payload carries the field names the endpoint projects, so the
  wire contract holds for the REAL sink and not only for the fake.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

# Register the table models so create_all() picks them up.
import daemon.repositories.mcps_gateway.models  # noqa: F401

from daemon.repositories.mcps_gateway.models import McpGatewayAudit
from daemon.repositories.mcps_gateway.repository import McpGatewayAuditRepository
from daemon.services.mcps_gateway.audit import AuditRecord

# The endpoint's row→wire projection. It is a module-private helper, but
# importing it here is what makes the assertion MEANINGFUL: it proves a
# row produced by the REAL query feeds the FE contract unchanged (the
# repo pins private helpers in tests elsewhere — e.g.
# tests/unit/tools/test_job_payload_mission_ref.py).
from daemon.routers.mcps_gateway_read import _audit_entry


# ────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine():
    """In-memory SQLite engine (StaticPool, session-scoped to a test)."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def repository(engine):
    """McpGatewayAuditRepository backed by the in-memory engine."""
    return McpGatewayAuditRepository(engine)


# ────────────────────────────────────────────────────────────────────────
# The ledger fixture
# ────────────────────────────────────────────────────────────────────────

#: Five audit rows with DISTINCT timestamps, in the order they are
#: INSERTED — deliberately NOT the chronological order. A read that
#: leaked the insertion order (no ORDER BY, or an ASC one) cannot pass
#: the assertions below.
_ROWS = (
    {
        "agent_id": "leader",
        "instance_id": "inst-42",
        "machine_id": "mac-air-03",
        "tool": "computer_type",
        "args_hash": "a1" * 32,
        "outcome": "ok",
        "session_id": "sess-9f2c1a",
        "error_code": "",
        "created_at": "2026-10-22T10:45:00.000000+00:00",
    },
    {
        "agent_id": "worker",
        "instance_id": "inst-07",
        "machine_id": "mac-studio-01",
        "tool": "computer_click",
        "args_hash": "b2" * 32,
        "outcome": "ok",
        "session_id": "sess-1a2b3c",
        "error_code": "",
        "created_at": "2026-10-22T07:00:00.000000+00:00",
    },
    {
        "agent_id": "leader",
        "instance_id": "inst-42",
        "machine_id": "mac-mini-02",
        "tool": "computer_screenshot",
        "args_hash": "c3" * 32,
        "outcome": "error",
        "session_id": "sess-4d5e6f",
        "error_code": "TOOL_TIMEOUT",
        "created_at": "2026-10-22T06:05:00.000000+00:00",
    },
    {
        "agent_id": "worker",
        "instance_id": "inst-07",
        "machine_id": "mac-studio-01",
        "tool": "computer_move",
        "args_hash": "d4" * 32,
        "outcome": "ok",
        "session_id": "sess-1a2b3c",
        "error_code": "",
        "created_at": "2026-10-22T08:15:00.000000+00:00",
    },
    {
        "agent_id": "leader",
        "instance_id": "",
        "machine_id": "mac-mini-02",
        "tool": "computer_key",
        "args_hash": "e5" * 32,
        "outcome": "denied",
        "session_id": "",
        "error_code": "SESSION_EXPIRED",
        "created_at": "2026-10-22T09:30:00.000000+00:00",
    },
)

#: The EXPECTED newest-first order, written out by hand (NOT derived
#: from a sort — a derived expectation would re-implement the code under
#: test).
_EXPECTED_NEWEST_FIRST = (
    "2026-10-22T10:45:00.000000+00:00",  # mac-air-03 / computer_type
    "2026-10-22T09:30:00.000000+00:00",  # mac-mini-02 / computer_key
    "2026-10-22T08:15:00.000000+00:00",  # mac-studio-01 / computer_move
    "2026-10-22T07:00:00.000000+00:00",  # mac-studio-01 / computer_click
    "2026-10-22T06:05:00.000000+00:00",  # mac-mini-02 / computer_screenshot
)


def _seed(repository: McpGatewayAuditRepository) -> None:
    """Write the five fixture rows through the REAL write path."""
    for row in _ROWS:
        repository.record(
            AuditRecord(
                agent_id=row["agent_id"],
                instance_id=row["instance_id"],
                machine_id=row["machine_id"],
                tool=row["tool"],
                args_hash=row["args_hash"],
                outcome=row["outcome"],
                session_id=row["session_id"],
                error_code=row["error_code"],
                created_at=row["created_at"],
            )
        )


def _stamps(rows: list[dict]) -> list[str]:
    """The ``created_at`` stamps of a result page, in order."""
    return [row["created_at"] for row in rows]


# ────────────────────────────────────────────────────────────────────────
# Ordering + pagination
# ────────────────────────────────────────────────────────────────────────


class TestListRecentOrderingAndPagination:
    """The real query path: NEWEST FIRST, ``limit``-capped, ``offset``-paged."""

    def test_limit_returns_the_head_of_the_newest_first_list(self, repository):
        """``limit=N`` returns exactly N rows, newest first."""
        _seed(repository)

        rows = repository.list_recent(limit=5, offset=0)

        assert len(rows) == 5
        assert _stamps(rows) == list(_EXPECTED_NEWEST_FIRST)

    def test_limit_caps_the_page(self, repository):
        """A limit smaller than the ledger returns only that many rows."""
        _seed(repository)

        rows = repository.list_recent(limit=2, offset=0)

        assert len(rows) == 2
        assert _stamps(rows) == list(_EXPECTED_NEWEST_FIRST[:2])

    def test_offset_returns_the_next_page_with_no_overlap_and_no_gap(
        self, repository
    ):
        """Paging from the HEAD of the newest-first list covers every row."""
        _seed(repository)

        page_one = repository.list_recent(limit=2, offset=0)
        page_two = repository.list_recent(limit=2, offset=2)
        page_three = repository.list_recent(limit=2, offset=4)

        assert _stamps(page_one) == list(_EXPECTED_NEWEST_FIRST[0:2])
        assert _stamps(page_two) == list(_EXPECTED_NEWEST_FIRST[2:4])
        assert _stamps(page_three) == list(_EXPECTED_NEWEST_FIRST[4:5])

        # No overlap between the pages…
        assert not set(_stamps(page_one)) & set(_stamps(page_two))
        assert not set(_stamps(page_two)) & set(_stamps(page_three))
        # …and no gap: the three pages cover the ledger exactly.
        assert set(_stamps(page_one + page_two + page_three)) == set(
            _EXPECTED_NEWEST_FIRST
        )

    def test_offset_past_the_end_is_an_empty_list_not_an_error(self, repository):
        """An offset beyond the ledger answers an empty page (the FE's
        "end of list"), never an exception."""
        _seed(repository)

        rows = repository.list_recent(limit=10, offset=5)

        assert rows == []

    def test_an_empty_ledger_is_an_empty_list(self, repository):
        """No rows at all — the same empty answer, no error."""
        assert repository.list_recent(limit=10, offset=0) == []

    def test_the_clamps_of_the_production_path_are_honoured(self, repository):
        """``limit`` is floored at 1 and ``offset`` at 0 by the repo.

        The router validates both bounds (422), so these clamps only ever
        fire on a DIRECT repository call — but they are the production
        path, so they are pinned here rather than left implicit.
        """
        _seed(repository)

        # limit=0 → 1 row (the newest), NOT zero rows and NOT an error.
        assert len(repository.list_recent(limit=0, offset=0)) == 1
        # offset=-1 → clamped to 0 → the same head page.
        assert _stamps(repository.list_recent(limit=2, offset=-1)) == list(
            _EXPECTED_NEWEST_FIRST[:2]
        )


class TestTieBreakPagingStability:
    """AD8 — the ``id DESC`` secondary sort buys PAGING STABILITY.

    Two rows sharing one ``created_at`` stamp are a REAL case (same
    second, sub-second writes): without a deterministic tiebreaker the
    DB is free to swap them BETWEEN two identical queries, so a page
    boundary could repeat a row on one page and skip it on the next.
    ``id`` is uuid4 TEXT — uncorrelated with time — so this pins the
    FIXED order, NOT a chronological claim within the tie.
    """

    _TIE_STAMP = "2026-10-22T12:00:00.000000+00:00"

    @pytest.fixture
    def tied_repository(self, repository):
        """Two rows with the SAME stamp, written through the real path."""
        repository.record(
            AuditRecord(
                agent_id="leader",
                machine_id="mac-a",
                tool="computer_type",
                args_hash="aa" * 32,
                created_at=self._TIE_STAMP,
            )
        )
        repository.record(
            AuditRecord(
                agent_id="worker",
                machine_id="mac-b",
                tool="computer_click",
                args_hash="bb" * 32,
                created_at=self._TIE_STAMP,
            )
        )
        return repository

    def test_the_tie_order_is_deterministic_across_reads(self, tied_repository):
        first = [row["machine_id"] for row in tied_repository.list_recent(limit=2)]
        second = [row["machine_id"] for row in tied_repository.list_recent(limit=2)]
        assert first == second
        assert sorted(first) == ["mac-a", "mac-b"]

    def test_paging_across_the_tie_returns_two_different_rows(self, tied_repository):
        """The pin that did not exist before: ``limit=1`` pages must
        not repeat a row (nor drop one) across the tie."""
        page_one = tied_repository.list_recent(limit=1, offset=0)
        page_two = tied_repository.list_recent(limit=1, offset=1)
        assert len(page_one) == 1
        assert len(page_two) == 1
        assert page_one[0]["id"] != page_two[0]["id"]
        assert {page_one[0]["machine_id"], page_two[0]["machine_id"]} == {
            "mac-a",
            "mac-b",
        }


class TestRepositoryRaisesWhenEngineDead:
    """AD5 — the repository no longer swallows read failures.

    The classification (the degraded body) lives in the SERVICE, not
    here; this pin keeps the contract from silently re-absorbing the
    exception at the wrong layer.
    """

    def test_audit_repository_raises_when_engine_dead(self):
        from sqlalchemy import create_engine

        from daemon.repositories.mcps_gateway.repository import (
            McpGatewayAuditRepository,
        )

        engine = create_engine("postgresql+psycopg://127.0.0.1:1/ensemble_dev_test_only")
        repository = McpGatewayAuditRepository(engine)
        try:
            with pytest.raises(Exception):  # noqa: B017, PT011 — ANY raise is the pin
                repository.list_recent(limit=10, offset=0)
        finally:
            engine.dispose()


# ────────────────────────────────────────────────────────────────────────
# The row payload contract
# ────────────────────────────────────────────────────────────────────────


class TestRowPayloadContract:
    """The rows the REAL query returns carry the projected field names."""

    def test_the_payload_carries_the_model_field_names(self, repository):
        """The row dict exposes the table's own column names.

        The endpoint projects by ``row.get(<column name>)`` — so a column
        renamed or dropped on the table would surface as a blank FE
        field. The payload keys are pinned against the MODEL, so a
        schema drift is caught here.
        """
        _seed(repository)

        row = repository.list_recent(limit=1, offset=0)[0]

        assert set(row) == set(McpGatewayAudit.model_fields)

    def test_the_values_round_trip(self, repository):
        """Every projected field carries the value that was written."""
        _seed(repository)

        row = repository.list_recent(limit=1, offset=0)[0]

        assert row["created_at"] == _ROWS[0]["created_at"]
        assert row["agent_id"] == _ROWS[0]["agent_id"]
        assert row["instance_id"] == _ROWS[0]["instance_id"]
        assert row["machine_id"] == _ROWS[0]["machine_id"]
        assert row["tool"] == _ROWS[0]["tool"]
        assert row["args_hash"] == _ROWS[0]["args_hash"]
        assert row["outcome"] == _ROWS[0]["outcome"]
        assert row["session_id"] == _ROWS[0]["session_id"]
        assert row["error_code"] is None  # the empty string → NULL

    def test_a_real_row_feeds_the_endpoint_projection(self, repository):
        """The wire entry built from a REAL row carries the FE names.

        This is the link the fake-sink endpoint tests could not make:
        ``created_at`` is renamed to ``timestamp`` and every other
        projected name passes through unchanged.
        """
        _seed(repository)

        row = repository.list_recent(limit=1, offset=0)[0]
        entry = _audit_entry(row)

        assert entry.timestamp == row["created_at"]
        assert entry.agent_id == _ROWS[0]["agent_id"]
        assert entry.instance_id == _ROWS[0]["instance_id"]
        assert entry.machine_id == _ROWS[0]["machine_id"]
        assert entry.tool == _ROWS[0]["tool"]
        assert entry.outcome == _ROWS[0]["outcome"]
        assert entry.session_id == _ROWS[0]["session_id"]
        assert entry.error_code is None
        assert entry.args_hash == _ROWS[0]["args_hash"]
