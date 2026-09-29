"""Unit tests for the daemon boot epoch (stop-frozen heartbeat amnesty anchor).

Incident r-20260929-170301-0cb2: a promote's own daemon stop froze
RUNNING task heartbeats; the replacement daemon's readiness probe
read the frozen beats as a CURRENT stall and the promote
auto-rolled-back. The boot epoch (daemon/services/boot_epoch.py) is
the single anchor both amnesty consumers (readiness
queue_freshness; StaleTaskRecovery's stale clock) clamp to. These
tests pin the epoch module's own contract: DB-clock capture,
first-wins idempotence, normalization, and fail-soft behavior.

The consumer-side predicates (readiness SQL, stale-finder gates) are
tested in ``tests/test_health_probes.py``,
``tests/message_queue_redesign/``, and the incident-replay E2E
``tests/e2e/test_promote_stop_frozen_amnesty_e2e.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from daemon.services import boot_epoch
from daemon.services.boot_epoch import (
    BOOT_EPOCH_FLOOR,
    capture_boot_epoch,
    get_boot_epoch,
    set_boot_epoch,
)


@pytest.fixture(autouse=True)
def _reset_epoch():
    """Isolate the process-global epoch per test."""
    set_boot_epoch(None)
    yield
    set_boot_epoch(None)


@pytest.fixture
def sqlite_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    yield engine
    engine.dispose()


class TestSetGet:
    def test_unset_is_none(self):
        assert get_boot_epoch() is None

    def test_set_get_roundtrip_naive(self):
        epoch = datetime(2026, 9, 29, 17, 5, 51)
        set_boot_epoch(epoch)
        assert get_boot_epoch() == epoch

    def test_set_normalizes_aware_to_naive_utc_digits(self):
        aware = datetime(
            2026, 9, 29, 17, 5, 51, tzinfo=timezone(timedelta(hours=7))
        )
        set_boot_epoch(aware)
        # Same instant, stored as naive-UTC digits (the heartbeat bind
        # frame — see daemon/services/timestamps.py DC-A).
        assert get_boot_epoch() == datetime(2026, 9, 29, 10, 5, 51)
        assert get_boot_epoch().tzinfo is None

    def test_set_none_clears(self):
        set_boot_epoch(datetime(2026, 9, 29, 17, 5, 51))
        set_boot_epoch(None)
        assert get_boot_epoch() is None

    def test_set_garbage_string_is_dropped(self):
        set_boot_epoch("not-a-timestamp")
        assert get_boot_epoch() is None

    def test_set_iso_string_is_parsed(self):
        set_boot_epoch("2026-09-29 17:05:51")
        assert get_boot_epoch() == datetime(2026, 9, 29, 17, 5, 51)

    def test_floor_is_year_one(self):
        # The sentinel every real heartbeat is at/after — binding it
        # must degenerate the epoch filter to a no-op.
        assert BOOT_EPOCH_FLOOR == datetime.min


class TestCapture:
    def test_capture_sqlite_sets_epoch_from_db_clock(self, sqlite_engine):
        before = datetime.now(timezone.utc).replace(tzinfo=None)
        captured = capture_boot_epoch(sqlite_engine)
        after = datetime.now(timezone.utc).replace(tzinfo=None)

        assert captured is not None
        assert captured.tzinfo is None
        # DB clock (CURRENT_TIMESTAMP is UTC): within the test's own
        # wall-clock bracket. CURRENT_TIMESTAMP has second precision —
        # allow a 1s slack on both ends.
        assert before - timedelta(seconds=1) <= captured <= after
        assert get_boot_epoch() == captured

    def test_capture_first_wins(self, sqlite_engine):
        first = capture_boot_epoch(sqlite_engine)
        assert first is not None
        # A second invocation (re-entered lifespan) must NOT move the
        # epoch — the epoch of a process is fixed at its boot.
        assert capture_boot_epoch(sqlite_engine) == first
        assert get_boot_epoch() == first

    def test_capture_respects_explicit_set(self, sqlite_engine):
        explicit = datetime(2026, 9, 29, 17, 5, 51)
        set_boot_epoch(explicit)
        assert capture_boot_epoch(sqlite_engine) == explicit

    def test_failed_capture_returns_none_and_leaves_epoch_unset(self):
        # A dead engine must not raise at boot and must not poison the
        # epoch — a later successful capture is still possible.
        broken = create_engine(
            "sqlite:////nonexistent-dir-no-perm/ensemble.db?mode=ro&uri=true",
            connect_args={"check_same_thread": False, "timeout": 0.01},
        )
        try:
            assert capture_boot_epoch(broken) is None
        finally:
            broken.dispose()
        assert get_boot_epoch() is None

    def test_failed_capture_does_not_block_later_success(self, sqlite_engine):
        broken = create_engine(
            "sqlite:////nonexistent-dir-no-perm/ensemble.db?mode=ro&uri=true",
            connect_args={"check_same_thread": False, "timeout": 0.01},
        )
        try:
            capture_boot_epoch(broken)
        finally:
            broken.dispose()
        captured = capture_boot_epoch(sqlite_engine)
        assert captured is not None
        assert get_boot_epoch() == captured
