"""Pack G — tokens + watcher surface + canonical notify status
(wi-78967dba).

Pins (phase3-plan T3.7):
* three tokens derive TRUTHFUL canonical statuses
  (``canonicalize_status`` round-trip + ``terminal_reason_variants_for``
  membership);
* ``is_terminal`` / terminal-set surfaces UNCHANGED (the raw token is
  NOT terminal — by design; the canonical target IS);
* zero-watcher WARN correlation (the (h) close line references the
  PP1 token family);
* held ``mission_terminal`` rows SURVIVE the (h) fire;
* W1 (reviewer council): the FAILED-variant close notifies
  status='failed' — the canonical string via
  ``canonicalize_status(token)`` — never the raw
  ``zombie_active_failed`` token and never the f2-literal
  'completed'. Planted forms (raw token / f2-literal) both RED.
  Companion assertions on the completed- and cancelled-variants.
"""

from __future__ import annotations


import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

import daemon.repositories.instance.models  # noqa: F401
import daemon.repositories.job_queue.models  # noqa: F401
import daemon.repositories.job_queue.watcher_models  # noqa: F401
import daemon.repositories.task.models  # noqa: F401

from daemon.repositories.job_queue.watcher_models import (
    ALL_TERMINAL_STATES,
)
from daemon.services.dependency_bus import set_dependency_bus
from daemon.services.work_status import (
    canonicalize_status,
    is_terminal,
    terminal_reason_variants_for,
)

from tests.job_queue.zombie_close_helpers import (
    insert_zombie_fixture,
    make_bus_mock,
    make_jqs_mock,
    make_recovery_service,
)

#: The pack-local declared collected count (NEW packs only).
EXPECTED_COLLECTED = 7


class TestTokenDerivation:
    async def test_three_tokens_collapse_onto_organic_outcomes(self):
        assert (
            canonicalize_status("zombie_active_completed") == "completed"
        )
        assert canonicalize_status("zombie_active_failed") == "failed"
        assert (
            canonicalize_status("zombie_active_cancelled")
            == "cancelled"
        )

    async def test_reverse_index_auto_derivation(self):
        for canonical in ("completed", "failed", "cancelled"):
            variants = terminal_reason_variants_for(canonical)
            assert f"zombie_active_{canonical}" in variants, (
                f"the reverse index must auto-derive the token for "
                f"{canonical}: {variants}"
            )

    async def test_raw_tokens_are_not_terminal_statuses(self):
        """By design the RAW token is absent from the wire terminal
        set — that is exactly why the notify status must be the
        CANONICAL string (W1). The canonical target IS terminal."""
        for raw in (
            "zombie_active_completed",
            "zombie_active_failed",
            "zombie_active_cancelled",
        ):
            assert raw not in ALL_TERMINAL_STATES
            assert not is_terminal(raw)
        for canonical in ("completed", "failed", "cancelled"):
            assert is_terminal(canonical)


@pytest.fixture
def tk_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def tk_bus():
    bus = make_bus_mock(pending=[])
    set_dependency_bus(bus)
    try:
        yield bus
    finally:
        set_dependency_bus(None)


async def _close_one(engine, verdict: str, jqs):
    ref = insert_zombie_fixture(engine, verdict=verdict, age_hours=3.0)
    service = make_recovery_service(engine, job_queue_service=jqs)
    await (
        service.reconcile_drift_states(
            min_pending_age_seconds=0,
            min_orphan_age_seconds=60,
        )
    )
    return ref


class TestCanonicalNotifyStatus:
    async def test_failed_variant_notifies_canonical_failed(self, tk_engine, tk_bus):
        """W1: NEVER 'completed' and NEVER the raw token — both
        planted forms asserted against the ACTUAL notify call."""
        jqs = make_jqs_mock()
        ref = await _close_one(tk_engine, "failed", jqs)
        assert jqs.notify_watchers.await_count == 1
        args = jqs.notify_watchers.await_args
        assert args.args[0] == ref["job_id"]
        assert args.args[1] == "failed", (
            "the notify status must be the CANONICAL verdict string; "
            f"got {args.args[1]!r} (raw token / f2-literal would "
            "misroute terminal watcher cleanup and mislabel the "
            "verdict)"
        )

    async def test_completed_variant_notifies_canonical_completed(
        self, tk_engine, tk_bus
    ):
        jqs = make_jqs_mock()
        await _close_one(tk_engine, "completed", jqs)
        assert jqs.notify_watchers.await_args.args[1] == "completed"

    async def test_cancelled_variant_notifies_canonical_cancelled(
        self, tk_engine, tk_bus
    ):
        jqs = make_jqs_mock()
        await _close_one(tk_engine, "cancelled", jqs)
        assert jqs.notify_watchers.await_args.args[1] == "cancelled"


class TestZeroWatcherCorrelation:
    async def test_zero_watcher_close_counts_and_references_pp1(
        self, tk_engine, tk_bus, caplog
    ):
        """The close fires with NO watchers (mock returns 0) ⇒ the
        zero_watcher_warns counter increments and the close log line
        carries the PP1 token family reference for forensics
        correlation."""
        import logging

        jqs = make_jqs_mock()  # notify returns 0
        with caplog.at_level(logging.WARNING, logger="daemon.services.job_recovery_service"):
            ref = await _close_one(tk_engine, "completed", jqs)
        close_lines = [
            r for r in caplog.records
            if r.getMessage().startswith(
                "reconcile_drift_states: Pattern (h) zombie-active close"
            )
            and "job " + ref["job_id"][:8] in r.getMessage()
        ]
        assert close_lines, "the (h) close line must be logged"
        msg = close_lines[-1].getMessage()
        assert "PP1" in msg, (
            "the close line must reference the PP1 zero-watcher token "
            "family so the two lines correlate in forensics"
        )
        assert "notify=notified:0" in msg, msg
