"""The idle-in-transaction census in the wedge capture (item 6 / part B).

Pins ``db_capture._read_activity_census`` + its wiring into
``capture_db_snapshot``:

* the ``pg_stat_activity`` query SHAPE — the grouped census (state
  bucketing incl. the NULL bucket, per-state oldest xact age) and the
  capped idle-in-txn detail (per-backend ``now() - xact_start`` ages);
* the mapping correctness — total backends, by-state counts, the
  idle-in-transaction block;
* the HARD CONSTRAINT: the census runs on the CAPTURE'S OWN
  connection — ``capture_db_snapshot`` opens exactly ONE one-shot
  connection and the census never opens another (amplification-free
  during a wedge);
* fail-open: a census-statement failure degrades the census, never the
  capture;
* the skipped-budget skeleton carries the (empty) census key.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from daemon.services.wedge_capture import db_capture
from daemon.services.wedge_capture.db_capture import (
    IDLE_IN_TXN_STATE,
    MAX_IDLE_IN_TXN_BACKENDS,
    UNKNOWN_STATE_BUCKET,
    capture_db_snapshot,
)

DSN = "postgresql://user:pw@127.0.0.1:1/nowhere"


def _norm(query: str) -> str:
    return " ".join(query.split())


class _ScriptedCursor:
    """Answers ``fetchall`` per query substring; records every execute."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, Any]] = []
        self._answers: list[tuple[str, Any]] = []

    def script(self, substring: str, answer: Any) -> None:
        self._answers.append((substring, answer))

    def execute(self, query: str, params: Any = None) -> None:
        self.executed.append((_norm(query), params))

    def fetchall(self) -> list[Any]:
        query = self.executed[-1][0]
        for substring, answer in reversed(self._answers):
            # REVERSE order: the LAST script for a substring wins, so a
            # test can override the happy-path rows for one statement.
            if substring in query:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        raise AssertionError(f"unscripted query: {query[:120]}")

    def __enter__(self) -> "_ScriptedCursor":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _ScriptedConnection:
    def __init__(self, cursor: _ScriptedCursor) -> None:
        self._cursor = cursor
        self.closed = False

    def cursor(self) -> _ScriptedCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


def _script_happy_capture(cursor: _ScriptedCursor) -> None:
    xact_start = datetime(2026, 10, 29, 11, 58, 0, tzinfo=timezone.utc)
    state_change = datetime(2026, 10, 29, 11, 58, 30, tzinfo=timezone.utc)
    cursor.script("FROM tasks t", [])  # executing turns
    cursor.script("FROM pg_class c", [])  # store census
    cursor.script(
        "GROUP BY 1",  # census statement 1 (post-CASE bucket shape)
        [
            ("active", 3, 12.5),
            ("idle", 5, None),
            (IDLE_IN_TXN_STATE, 2, 91.4),
            (UNKNOWN_STATE_BUCKET, 4, None),
        ],
    )
    cursor.script(
        "WHERE state = %s",  # census statement 2 (the capped detail)
        [
            (
                4242,
                "ensemble",
                "",
                "client backend",
                xact_start,
                state_change,
                91.4,
            ),
            (4243, "ensemble", "wedge-capture", "client backend", None, None, 45.0),
        ],
    )
    cursor.script("auto_continued_at", [])  # resume history


def _run_capture(
    cursor: _ScriptedCursor, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    connection = _ScriptedConnection(cursor)
    connects: list[str] = []

    def _fake_connect(dsn: str, *, connect_timeout_s: int, statement_timeout_ms: int):
        connects.append(dsn)
        return connection

    monkeypatch.setattr(db_capture, "_connect", _fake_connect)
    payload = capture_db_snapshot(DSN, boot_epoch="2026-10-29T12:00:00")
    payload["_connect_calls"] = len(connects)  # test-local provenance
    return payload


class TestCensusQueryShapeAndMapping:
    def test_both_statements_run_and_the_mapping_is_correct(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cursor = _ScriptedCursor()
        _script_happy_capture(cursor)
        payload = _run_capture(cursor, monkeypatch)

        assert payload["status"] == "ok"
        assert payload["_connect_calls"] == 1
        census = payload["activity_census"]
        assert census["status"] == "ok"

        # The executed statements, in order: executing turns, store
        # census, census stmt 1, census stmt 2, resume history.
        assert len(cursor.executed) == 5
        grouped_query, grouped_params = cursor.executed[2]
        assert "FROM pg_stat_activity" in grouped_query
        assert "GROUP BY 1" in grouped_query
        # The NULL-state bucket (background/auxiliary backends) is
        # bucketed server-side, and the oldest xact age per state is
        # computed server-side too (now() - xact_start, seconds).
        assert "CASE WHEN state IS NULL" in grouped_query
        assert grouped_params == (UNKNOWN_STATE_BUCKET,)
        assert (
            "MAX(EXTRACT(EPOCHFROM(now()-xact_start)))::doubleprecision"
            in grouped_query.replace(" ", "")
        )
        detail_query, detail_params = cursor.executed[3]
        assert "FROM pg_stat_activity" in detail_query
        assert "WHERE state = %s" in detail_query
        assert (
            "EXTRACT(EPOCHFROM(now()-xact_start))::doubleprecision"
            in detail_query.replace(" ", "")
        )
        assert detail_params == (IDLE_IN_TXN_STATE, MAX_IDLE_IN_TXN_BACKENDS)

        # State bucketing + total backends.
        assert census["by_state"] == {
            "active": 3,
            "idle": 5,
            IDLE_IN_TXN_STATE: 2,
            UNKNOWN_STATE_BUCKET: 4,
        }
        assert census["total_backends"] == 14  # the sum of the buckets

        # The idle-in-transaction block: count + the OLDEST age.
        idle = census["idle_in_transaction"]
        assert idle["count"] == 2
        assert idle["oldest_xact_age_seconds"] == 91.4

        # The per-backend detail: ages extracted, ISO timestamps, the
        # truncated flag clear under the cap.
        backends = idle["backends"]
        assert len(backends) == 2
        assert backends[0] == {
            "pid": 4242,
            "usename": "ensemble",
            "application_name": "",
            "backend_type": "client backend",
            "xact_start": "2026-10-29T11:58:00Z",
            "state_change": "2026-10-29T11:58:30Z",
            "xact_age_seconds": 91.4,
        }
        assert backends[1]["xact_start"] is None
        assert backends[1]["xact_age_seconds"] == 45.0
        assert idle["backends_truncated"] is False

    def test_the_detail_cap_sets_the_truncated_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cursor = _ScriptedCursor()
        _script_happy_capture(cursor)
        rows = [
            (1000 + i, "ensemble", "", "client backend", None, None, float(i))
            for i in range(MAX_IDLE_IN_TXN_BACKENDS)
        ]
        cursor.script("WHERE state = %s", rows)
        payload = _run_capture(cursor, monkeypatch)
        idle = payload["activity_census"]["idle_in_transaction"]
        assert len(idle["backends"]) == MAX_IDLE_IN_TXN_BACKENDS
        assert idle["backends_truncated"] is True


class TestOwnConnectionConstraint:
    def test_the_capture_opens_exactly_one_connection_and_the_census_rides_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cursor = _ScriptedCursor()
        _script_happy_capture(cursor)
        payload = _run_capture(cursor, monkeypatch)
        # ONE one-shot connection, opened once. Every census statement
        # went through THAT connection's cursor (the same scripted
        # cursor answered all five statements) — the census never
        # opened a second connection or touched any pool.
        assert payload["_connect_calls"] == 1
        census_queries = [
            q for q, _ in cursor.executed if "pg_stat_activity" in q
        ]
        assert len(census_queries) == 2


class TestCensusFailOpen:
    def test_statement1_failure_degrades_the_whole_census_never_the_capture(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cursor = _ScriptedCursor()
        _script_happy_capture(cursor)
        cursor.script("GROUP BY 1", RuntimeError("pg_stat_activity locked"))
        payload = _run_capture(cursor, monkeypatch)
        # The CAPTURE is unaffected.
        assert payload["status"] == "ok"
        assert payload["executing_turns_count"] == 0
        # The census degraded to the typed failure shape.
        assert payload["activity_census"] == {"status": "failed:RuntimeError"}

    def test_statement2_failure_keeps_the_grouped_census(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cursor = _ScriptedCursor()
        _script_happy_capture(cursor)
        cursor.script("WHERE state = %s", RuntimeError("statement timeout"))
        payload = _run_capture(cursor, monkeypatch)
        census = payload["activity_census"]
        assert census["status"] == "ok"
        assert census["total_backends"] == 14
        assert census["by_state"][IDLE_IN_TXN_STATE] == 2
        idle = census["idle_in_transaction"]
        assert idle["count"] == 2
        assert idle["backends"] == []
        assert idle["backends_status"] == "failed:RuntimeError"


class TestSkippedBudgetSkeleton:
    def test_the_skipped_skeleton_carries_the_empty_census_key(self) -> None:
        payload = capture_db_snapshot(DSN, boot_epoch="2026-10-29T12:00:00",
                                      budget_s=0.0)
        assert payload["status"] == "skipped"
        assert payload["activity_census"] == {}
