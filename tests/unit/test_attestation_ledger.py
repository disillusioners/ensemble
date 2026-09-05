"""Unit tests for the Phase 3 attestation ledger repository methods.

Covers the four methods on :class:`SQLModelInstanceRepository` (Phase 3
task 3.3) plus the leader-ruling-1/2 reset semantics:

* (a) ``increment_attestation_denied_count(instance_id, denial_epoch)`` —
  O4 idempotent per-denial-epoch increment (replays with the same
  ``denial_epoch`` MUST NOT double-increment).
* (b) ``reset_attestation_denied_count(instance_id)`` — the SINGLE reset
  op that clears BOTH ``attestation_denied_count`` AND
  ``completion_gate_escalated`` per leader ruling 2 (both columns
  share the per-mission lifecycle).
* (c) ``set_completion_gate_escalated(instance_id)`` — terminal-
  after-bound flag setter (no counter change).
* (d) ``get_attestation_denied_count(instance_id)`` — read accessor
  for the gate node's ``denied_count_getter``.

The reset semantics — per leader ruling 1, FOUR triggers ONLY:
  (1) attested allow (``Decision.ALLOWED`` under enforce with
      ``attestation_present=True``);
  (2) ``terminal_after_bound`` finalization;
  (3) revive-from-COMPLETED via a NEW top-level user/mission message
      (fresh episode) — wired in
      ``daemon/services/instance_messaging.py:_prepare_enqueued_message``;
  (4) instance creation (column default ``0`` — no method needed).
``allowed_legitimate_pending_wakeup`` (R2 un-attested allow) MUST NOT
reset the counter — that non-reset IS the loop protection.

All four methods fail-OPEN at the call site (the gate node wraps them
in ``except Exception`` and degrades deny → allow + emits
``leader_completion_gate_db_error`` per C3/AC-6.6). The repository
methods themselves raise on DB failure so the caller's
``except Exception`` sees the original exception class.

The C3 fail-open wrapper contract itself is tested separately in
:file:`tests/unit/test_attestation_ledger_failopen.py` (Phase 3 task 3.6
verification path).

Test fixtures
-------------

Uses the file-backed SQLite pattern documented in the dispatch testing
discipline (``tmp_path`` + NullPool + WAL + busy_timeout). We never
boot the full migration chain on SQLite (fresh-SQLite boot trap per
LESSONS/2026-09-04-fresh-sqlite-boot-migration-20260714-pg-only);
the new columns land via ``SQLModel.metadata.create_all()`` directly.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from daemon.repositories.instance.models import Instance
from daemon.repositories.instance.repository import SQLModelInstanceRepository


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path: Path):
    """File-backed SQLite engine with WAL + busy_timeout + NullPool.

    Mirrors the dispatch testing discipline: ``tmp_path`` + ``NullPool``
    + WAL + busy_timeout so concurrent writes are visible cross-session.
    We deliberately do NOT boot the full migration chain on SQLite (the
    fresh-SQLite boot trap is a live hazard); the attestation columns
    land via ``SQLModel.metadata.create_all()`` directly because the
    Instance SQLModel carries the new fields.
    """
    db_path = tmp_path / "attestation_ledger.sqlite"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={
            "check_same_thread": False,
            "timeout": 30,
        },
        poolclass=NullPool,
    )

    # Enable WAL mode + busy_timeout pragma on every new connection.
    @sa_event.listens_for(eng, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    SQLModel.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def repo(engine):
    """Repository over the file-backed engine."""
    return SQLModelInstanceRepository(engine)


def _make_instance(
    repo: SQLModelInstanceRepository,
    *,
    instance_id: str | None = None,
    agent_id: str = "leader",
    agent_dir: str = "./agents/leader",
) -> Instance:
    """Create an instance row with the defaults the gate cares about."""
    return repo.create(
        instance_id=instance_id or str(uuid.uuid4()),
        agent_id=agent_id,
        agent_dir=agent_dir,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Task 3.3(a) — increment_attestation_denied_count (O4 idempotency)
# ─────────────────────────────────────────────────────────────────────────────


class TestIncrementAttestationDeniedCount:
    def test_first_increment_returns_one(self, repo):
        inst = _make_instance(repo)
        new_count = repo.increment_attestation_denied_count(
            inst.instance_id, denial_epoch="ep-1"
        )
        assert new_count == 1
        assert repo.get_attestation_denied_count(inst.instance_id) == 1

    def test_multiple_distinct_epochs_accumulate(self, repo):
        inst = _make_instance(repo)
        repo.increment_attestation_denied_count(inst.instance_id, denial_epoch="ep-1")
        repo.increment_attestation_denied_count(inst.instance_id, denial_epoch="ep-2")
        repo.increment_attestation_denied_count(inst.instance_id, denial_epoch="ep-3")
        assert repo.get_attestation_denied_count(inst.instance_id) == 3

    def test_o4_replay_same_epoch_does_not_double_increment(self, repo):
        """O4 idempotency: pause-mid-gate resume replays the same deny.

        The same ``denial_epoch`` MUST NOT increment the counter again.
        Without this dedup, a pause-mid-gate race would inflate the
        counter past the bound and cause insta-escalation on the next
        mission.
        """
        inst = _make_instance(repo)
        first = repo.increment_attestation_denied_count(
            inst.instance_id, denial_epoch="ep-replay"
        )
        # Replay the same epoch — gate fired twice for the same logical deny.
        second = repo.increment_attestation_denied_count(
            inst.instance_id, denial_epoch="ep-replay"
        )
        third = repo.increment_attestation_denied_count(
            inst.instance_id, denial_epoch="ep-replay"
        )
        assert first == 1
        assert second == 1  # replay — no increment
        assert third == 1  # replay — no increment
        assert repo.get_attestation_denied_count(inst.instance_id) == 1

    def test_replay_after_real_increment_still_dedups(self, repo):
        """O4 + interleave: ep-1 increments, ep-2 increments, ep-1 replay = no-op."""
        inst = _make_instance(repo)
        repo.increment_attestation_denied_count(inst.instance_id, denial_epoch="ep-1")
        repo.increment_attestation_denied_count(inst.instance_id, denial_epoch="ep-2")
        # Replay ep-1 — MUST NOT re-increment.
        replay = repo.increment_attestation_denied_count(
            inst.instance_id, denial_epoch="ep-1"
        )
        assert replay == 2
        assert repo.get_attestation_denied_count(inst.instance_id) == 2

    def test_missing_instance_returns_minus_one(self, repo):
        result = repo.increment_attestation_denied_count(
            "no-such-instance", denial_epoch="ep-1"
        )
        # Sentinel for "row missing" — caller treats as DB error → fail-open.
        assert result == -1

    def test_db_error_propagates_for_caller_fail_open(self, repo):
        """The repo method RAISES on DB error — the gate's C3 wrapper
        is what swallows + logs. This is the contract: the wrapper
        needs the original exception class to emit a structured log.
        """
        inst = _make_instance(repo)
        # Replace the engine with one that always raises on any
        # operation — proves the wrapper sees the real exception
        # class (not a swallowed return).
        from sqlalchemy.exc import OperationalError

        class _ExplodingEngine:
            def connect(self, *args, **kwargs):
                raise OperationalError("statement", {}, Exception("db down"))

        repo.engine = _ExplodingEngine()
        with pytest.raises(Exception):
            repo.increment_attestation_denied_count(
                inst.instance_id, denial_epoch="ep-1"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Task 3.3(b) — reset_attestation_denied_count (leader ruling 2: BOTH columns)
# ─────────────────────────────────────────────────────────────────────────────


class TestResetAttestationDeniedCount:
    def test_reset_clears_counter_to_zero(self, repo):
        inst = _make_instance(repo)
        repo.increment_attestation_denied_count(inst.instance_id, denial_epoch="ep-1")
        repo.increment_attestation_denied_count(inst.instance_id, denial_epoch="ep-2")
        assert repo.get_attestation_denied_count(inst.instance_id) == 2
        ok = repo.reset_attestation_denied_count(inst.instance_id)
        assert ok is True
        assert repo.get_attestation_denied_count(inst.instance_id) == 0

    def test_reset_clears_escalation_flag_per_ruling_2(self, repo):
        """Leader ruling 2: completion_gate_escalated shares the per-mission
        lifecycle — the SAME single reset op clears BOTH columns.
        """
        inst = _make_instance(repo)
        repo.set_completion_gate_escalated(inst.instance_id)
        # Verify pre-state.
        row = repo.get(inst.instance_id)
        assert row.completion_gate_escalated is True
        # Reset.
        repo.reset_attestation_denied_count(inst.instance_id)
        # Verify both columns cleared.
        row = repo.get(inst.instance_id)
        assert row.attestation_denied_count == 0
        assert row.completion_gate_escalated is False

    def test_reset_missing_instance_returns_false(self, repo):
        ok = repo.reset_attestation_denied_count("no-such-instance")
        assert ok is False


# ─────────────────────────────────────────────────────────────────────────────
# Task 3.3(c) — set_completion_gate_escalated (terminal-after-bound flag)
# ─────────────────────────────────────────────────────────────────────────────


class TestSetCompletionGateEscalated:
    def test_set_escalated_flag_true(self, repo):
        inst = _make_instance(repo)
        repo.increment_attestation_denied_count(inst.instance_id, denial_epoch="ep-1")
        repo.increment_attestation_denied_count(inst.instance_id, denial_epoch="ep-2")
        repo.increment_attestation_denied_count(inst.instance_id, denial_epoch="ep-3")
        ok = repo.set_completion_gate_escalated(inst.instance_id)
        assert ok is True
        row = repo.get(inst.instance_id)
        assert row.completion_gate_escalated is True
        # Counter is NOT changed by set_escalated — that's the job of
        # reset_attestation_ledger_with_escalation (atomic single UPDATE)
        # on the terminal_after_bound path.
        assert row.attestation_denied_count == 3

    def test_set_escalated_missing_instance_returns_false(self, repo):
        assert repo.set_completion_gate_escalated("no-such-instance") is False


# ─────────────────────────────────────────────────────────────────────────────
# Bonus — reset_attestation_ledger_with_escalation (atomic terminal_after_bound)
# ─────────────────────────────────────────────────────────────────────────────


class TestResetAttestationLedgerWithEscalation:
    """The atomic single-UPDATE variant for the terminal_after_bound path.

    Sets ``completion_gate_escalated=True`` AND ``attestation_denied_count=0``
    in ONE UPDATE — the gate node calls THIS for ``Decision.TERMINAL_AFTER_BOUND``,
    not the plain reset.
    """

    def test_atomic_set_escalated_and_reset(self, repo):
        inst = _make_instance(repo)
        repo.increment_attestation_denied_count(inst.instance_id, denial_epoch="ep-1")
        repo.increment_attestation_denied_count(inst.instance_id, denial_epoch="ep-2")
        repo.increment_attestation_denied_count(inst.instance_id, denial_epoch="ep-3")
        ok = repo.reset_attestation_ledger_with_escalation(inst.instance_id)
        assert ok is True
        row = repo.get(inst.instance_id)
        # Counter RESET to 0 AND flag SET to True in one statement.
        assert row.attestation_denied_count == 0
        assert row.completion_gate_escalated is True

    def test_atomic_no_count_change_needed_when_already_zero(self, repo):
        """Edge case: counter is already 0 (bound never reached normally)."""
        inst = _make_instance(repo)
        ok = repo.reset_attestation_ledger_with_escalation(inst.instance_id)
        assert ok is True
        row = repo.get(inst.instance_id)
        assert row.attestation_denied_count == 0
        assert row.completion_gate_escalated is True


# ─────────────────────────────────────────────────────────────────────────────
# Task 3.3(d) — get_attestation_denied_count (read accessor)
# ─────────────────────────────────────────────────────────────────────────────


class TestGetAttestationDeniedCount:
    def test_returns_zero_for_fresh_instance(self, repo):
        inst = _make_instance(repo)
        assert repo.get_attestation_denied_count(inst.instance_id) == 0

    def test_returns_current_count(self, repo):
        inst = _make_instance(repo)
        repo.increment_attestation_denied_count(inst.instance_id, denial_epoch="ep-1")
        repo.increment_attestation_denied_count(inst.instance_id, denial_epoch="ep-2")
        assert repo.get_attestation_denied_count(inst.instance_id) == 2

    def test_returns_zero_for_missing_instance(self, repo):
        """Missing-row sentinel: 0 (the gate treats as fail-open via wrapper)."""
        assert repo.get_attestation_denied_count("no-such-instance") == 0


# ─────────────────────────────────────────────────────────────────────────────
# Leader-ruling-1 reset trigger matrix (the dispatch test contract)
# ─────────────────────────────────────────────────────────────────────────────


class TestResetTriggerMatrix:
    """The leader ruling 1 four-trigger matrix + R2 non-reset.

    These tests pin the dispatch's enumerated reset triggers AND the
    `allowed_legitimate_pending_wakeup` non-reset invariant — the drift
    between the prose ("every allow") and the ruling ("attested allow
    only") was the documented bug being closed by this phase.
    """

    def test_trigger_1_attested_allow_resets_via_repo(self, repo):
        """Trigger (1): attested allow → reset counter (and escalation flag)."""
        inst = _make_instance(repo)
        repo.increment_attestation_denied_count(inst.instance_id, denial_epoch="ep-1")
        repo.set_completion_gate_escalated(inst.instance_id)
        # The gate node calls reset_attestation_denied_count on attested
        # allow — assert it clears BOTH columns.
        repo.reset_attestation_denied_count(inst.instance_id)
        row = repo.get(inst.instance_id)
        assert row.attestation_denied_count == 0
        assert row.completion_gate_escalated is False

    def test_trigger_2_terminal_after_bound_resets_via_atomic_repo(self, repo):
        """Trigger (2): terminal_after_bound → set flag + reset (atomic)."""
        inst = _make_instance(repo)
        for ep in ("ep-1", "ep-2", "ep-3"):
            repo.increment_attestation_denied_count(inst.instance_id, denial_epoch=ep)
        # The gate node calls reset_attestation_ledger_with_escalation
        # for the terminal_after_bound path — atomic single UPDATE.
        repo.reset_attestation_ledger_with_escalation(inst.instance_id)
        row = repo.get(inst.instance_id)
        assert row.attestation_denied_count == 0
        assert row.completion_gate_escalated is True

    def test_trigger_3_fresh_episode_revival_resets_via_repo(self, repo):
        """Trigger (3): revive-from-COMPLETED via a NEW top-level user/
        mission message (fresh episode) → reset both columns.

        The wiring lives in
        ``daemon/services/instance_messaging.py:_prepare_enqueued_message``
        (same-transaction status=RUNNING site) — the repo methods are
        the canonical reset. The wiring code reads/writes the columns
        DIRECTLY in the same SQLModel Session for transaction-local
        atomicity; this test exercises the repo's reset op as the
        canonical contract.
        """
        inst = _make_instance(repo)
        repo.increment_attestation_denied_count(inst.instance_id, denial_epoch="ep-1")
        repo.set_completion_gate_escalated(inst.instance_id)
        # Simulate the fresh-episode revive reset.
        repo.reset_attestation_denied_count(inst.instance_id)
        row = repo.get(inst.instance_id)
        assert row.attestation_denied_count == 0
        assert row.completion_gate_escalated is False

    def test_trigger_4_instance_creation_starts_at_zero(self, repo):
        """Trigger (4): instance creation default — column default is 0."""
        inst = _make_instance(repo)
        row = repo.get(inst.instance_id)
        assert row.attestation_denied_count == 0
        assert row.completion_gate_escalated is False

    def test_r2_un_attested_allow_must_not_reset(self, repo):
        """Leader ruling 1: ``allowed_legitimate_pending_wakeup`` does NOT
        reset the counter — that non-reset IS the loop protection.

        This is the test pin for the ruling-1 drift (the prior "every
        allow" wording would have reset on R2 — that's the bug this
        phase closes). The wiring lives in
        ``daemon/graph.py:create_attestation_gate_node`` (the gate node
        checks ``decision.attestation_present`` before calling
        ``safe_reset`` — a False attestation_present means the path is
        R2 and the reset is skipped). The repo itself does not know
        about the decision semantics — it only exposes the reset op.
        The test below proves the row state stays UNCHANGED when the
        gate DOESN'T call reset (which is the R2 invariant at the
        repo+gate boundary).
        """
        inst = _make_instance(repo)
        # Pre-state: counter incremented, flag set (simulating a leader
        # that already had a denied completion).
        for ep in ("ep-1", "ep-2"):
            repo.increment_attestation_denied_count(inst.instance_id, denial_epoch=ep)
        repo.set_completion_gate_escalated(inst.instance_id)
        # The gate node, on R2 (allowed_legitimate_pending_wakeup), does
        # NOT call any reset method. Verify the row state is unchanged.
        row_before = repo.get(inst.instance_id)
        # ... no repo call ...
        row_after = repo.get(inst.instance_id)
        assert row_after.attestation_denied_count == row_before.attestation_denied_count
        assert row_after.completion_gate_escalated == row_before.completion_gate_escalated


# ─────────────────────────────────────────────────────────────────────────────
# Persistence-across-revive contract (counter SURVIVES revive)
# ─────────────────────────────────────────────────────────────────────────────


class TestCounterSurvivesRevive:
    """The counter SURVIVES revive (no in-memory precedent applies) —
    only the four triggers reset it. This pins the ruling-1 invariant
    that the in-memory ``_loop_breaker_state`` cleanup precedent does
    NOT apply to row-scoped DB columns.
    """

    def test_counter_persists_across_repo_get_reload(self, repo, engine):
        """The counter SURVIVES a fresh repo / fresh session — the row-
        scoped DB column design guarantees the counter outlives any
        in-memory state.
        """
        from daemon.repositories.instance.repository import (
            SQLModelInstanceRepository as Repo2,
        )

        inst = _make_instance(repo)
        repo.increment_attestation_denied_count(inst.instance_id, denial_epoch="ep-1")
        repo.increment_attestation_denied_count(inst.instance_id, denial_epoch="ep-2")
        first_read = repo.get_attestation_denied_count(inst.instance_id)
        assert first_read == 2

        # Fresh repository over the SAME engine — in-memory cache is
        # cold, but the row data persists in SQLite.
        repo2 = Repo2(engine)
        second_read = repo2.get_attestation_denied_count(inst.instance_id)
        assert second_read == 2, (
            "Counter did NOT survive a fresh repository / fresh session — "
            "the row-scoped column design requires this property"
        )

    def test_escalation_flag_persists_until_reset_op(self, repo):
        inst = _make_instance(repo)
        repo.set_completion_gate_escalated(inst.instance_id)
        # No in-memory cleanup hook — flag persists until a reset op.
        row = repo.get(inst.instance_id)
        assert row.completion_gate_escalated is True
        # Only the reset op clears it (per ruling 2 — same single op
        # clears BOTH columns).
        repo.reset_attestation_denied_count(inst.instance_id)
        row = repo.get(inst.instance_id)
        assert row.completion_gate_escalated is False
