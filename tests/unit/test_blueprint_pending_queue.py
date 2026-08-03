"""Unit tests for the pending-experience queue (C3) and helpers.

Phase 2 of the Project Blueprint evolution. Covers the durable
claim/acknowledge contract:

* ``enqueue`` inserts rows with status ``available``.
* ``claim_batch`` atomically grabs the N oldest claimable rows and
  stamps them with the run token (oldest-first).
* ``acknowledge_batch`` only honours rows owned by the matching
  ``run_token`` (token scoping).
* ``mark_retryable`` transitions ``claimed`` rows whose lease has
  expired to ``retryable`` (or ``abandoned`` past the retry cap).
* ``prune_processed`` hard-deletes rows whose ``processed_at`` is
  older than the threshold (crash recovery for the soft-delete).
* ``prune_excess`` caps the per-project pending rows (FIFO on
  unprocessed rows).
* Records NOT in the ack list remain ``claimed`` until lease timeout.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from daemon.repositories.blueprint.pending_models import (
    BlueprintPendingUpdate,
    PENDING_STATUS_ABANDONED,
    PENDING_STATUS_APPLIED,
    PENDING_STATUS_AVAILABLE,
    PENDING_STATUS_CLAIMED,
    PENDING_STATUS_RETRYABLE,
)
from daemon.repositories.blueprint.pending_repository import (
    BlueprintPendingRepository,
)


@pytest.fixture
def engine():
    """A fresh in-memory SQLite engine with all SQLModel tables created."""
    e = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(e)
    return e


@pytest.fixture
def repo(engine):
    return BlueprintPendingRepository(engine)


# ── Enqueue / basic CRUD ──────────────────────────────────────────


def test_enqueue_creates_available_row(repo):
    r = repo.enqueue("p1", "experience", {"text": "first"})
    assert r.status == PENDING_STATUS_AVAILABLE
    assert r.project_id == "p1"
    assert r.source_type == "experience"
    assert r.source_payload == {"text": "first"}
    assert r.run_token is None
    assert r.claimed_at is None
    assert r.processed_at is None
    assert r.retry_count == 0
    assert r.created_at  # non-empty


def test_get_pending_count_only_active_statuses(repo):
    repo.enqueue("p1", "experience", {"text": "a"})
    repo.enqueue("p1", "history", {"text": "b"})
    assert repo.get_pending_count("p1") == 2

    # Claim both, then ack only one. After the ack:
    #   - r1 = applied  → not counted
    #   - r2 = claimed  → not counted (CLAIMED is NOT in the
    #     active set; only AVAILABLE + RETRYABLE are).
    r1 = repo.list_pending("p1")[0]
    r2 = repo.list_pending("p1")[1]
    repo.claim_batch("p1", run_token="run-1")
    assert repo.get_pending_count("p1") == 0  # both claimed, none active
    repo.acknowledge_batch("run-1", [r1.id])
    assert repo.get_pending_count("p1") == 0  # r1 applied, r2 still claimed

    # Abandon r2 → still CLAIMED → not counted.
    repo.abandon_batch("run-1")
    assert repo.get_pending_count("p1") == 0


def test_list_pending_oldest_first(repo):
    r1 = repo.enqueue("p1", "experience", {"text": "first"})
    r2 = repo.enqueue("p1", "experience", {"text": "second"})
    r3 = repo.enqueue("p1", "history", {"text": "third"})
    rows = repo.list_pending("p1")
    assert [r.id for r in rows] == [r1.id, r2.id, r3.id]


# ── Claim / acknowledge ──────────────────────────────────────────


def test_claim_batch_oldest_first_sets_run_token(repo):
    r1 = repo.enqueue("p1", "experience", {"text": "first"})
    r2 = repo.enqueue("p1", "experience", {"text": "second"})
    r3 = repo.enqueue("p1", "history", {"text": "third"})

    claimed = repo.claim_batch("p1", batch_size=2, run_token="run-1")
    assert [r.id for r in claimed] == [r1.id, r2.id]
    assert all(r.status == PENDING_STATUS_CLAIMED for r in claimed)
    assert all(r.run_token == "run-1" for r in claimed)
    assert all(r.claimed_at is not None for r in claimed)

    # Third row still available.
    left = repo.list_pending("p1")
    assert len(left) == 1
    assert left[0].id == r3.id
    assert left[0].status == PENDING_STATUS_AVAILABLE


def test_claim_batch_partial_when_fewer_available(repo):
    repo.enqueue("p1", "experience", {"text": "only"})
    claimed = repo.claim_batch("p1", batch_size=50, run_token="run-1")
    assert len(claimed) == 1
    assert claimed[0].status == PENDING_STATUS_CLAIMED


def test_claim_batch_empty_when_nothing_available(repo):
    claimed = repo.claim_batch("p1", batch_size=10, run_token="run-1")
    assert claimed == []


def test_acknowledge_batch_token_scoped(repo):
    r1 = repo.enqueue("p1", "experience", {"text": "a"})
    r2 = repo.enqueue("p1", "experience", {"text": "b"})

    # Worker A claims both.
    repo.claim_batch("p1", run_token="run-A")
    # Worker B (wrong token) tries to ack → 0 rows.
    assert repo.acknowledge_batch("run-B", [r1.id, r2.id]) == 0
    # Worker A (right token) acks → 2 rows.
    assert repo.acknowledge_batch("run-A", [r1.id, r2.id]) == 2
    for record_id in (r1.id, r2.id):
        rec = repo.get_by_id(record_id)
        assert rec.status == PENDING_STATUS_APPLIED
        assert rec.processed_at is not None


def test_acknowledge_batch_partial_only_acks_named_ids(repo):
    r1 = repo.enqueue("p1", "experience", {"text": "a"})
    r2 = repo.enqueue("p1", "experience", {"text": "b"})

    repo.claim_batch("p1", run_token="run-A")
    # Only ack r1 — r2 stays claimed.
    assert repo.acknowledge_batch("run-A", [r1.id]) == 1
    assert repo.get_by_id(r1.id).status == PENDING_STATUS_APPLIED
    assert repo.get_by_id(r2.id).status == PENDING_STATUS_CLAIMED


def test_acknowledge_batch_claims_all_for_token_if_ids_none(repo):
    repo.enqueue("p1", "experience", {"text": "a"})
    repo.enqueue("p1", "experience", {"text": "b"})

    repo.claim_batch("p1", run_token="run-A")
    assert repo.acknowledge_batch("run-A") == 2


def test_abandon_batch_token_scoped(repo):
    repo.enqueue("p1", "experience", {"text": "a"})
    repo.enqueue("p1", "experience", {"text": "b"})
    repo.claim_batch("p1", run_token="run-A")

    # Wrong token: 0 rows.
    assert repo.abandon_batch("run-B") == 0
    # Right token: 2 rows → abandoned.
    assert repo.abandon_batch("run-A") == 2
    rows = repo.list_pending("p1")
    assert all(r.status == PENDING_STATUS_ABANDONED for r in rows)


def test_claim_batch_read_back_excludes_other_callers_rows(repo):
    """The read-back after claim must only return rows THIS caller
    claimed (run_token + status='claimed' guard), not phantom rows
    from a concurrent caller."""
    r1 = repo.enqueue("p1", "experience", {"text": "first"})
    r2 = repo.enqueue("p1", "experience", {"text": "second"})

    # Caller A claims both.
    claimed_a = repo.claim_batch("p1", batch_size=2, run_token="run-A")
    assert len(claimed_a) == 2
    assert all(r.run_token == "run-A" for r in claimed_a)

    # Caller B tries to claim — nothing left (both already claimed).
    # The read-back must return empty, NOT the rows claimed by A.
    claimed_b = repo.claim_batch("p1", batch_size=2, run_token="run-B")
    assert claimed_b == []


# ── Lease timeout / retry transitions ─────────────────────────────


def test_claim_batch_picks_up_retryable_after_lease_timeout(repo, engine):
    """After ``mark_retryable`` sweeps expired leases, the next
    ``claim_batch`` re-claims the same rows with the new run token."""
    r1 = repo.enqueue("p1", "experience", {"text": "a"})
    repo.claim_batch("p1", run_token="run-A")

    # Backdate claimed_at to 2 hours ago so the lease is way past the
    # 30-min default.
    backdate = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with Session(engine) as s:
        rec = s.get(BlueprintPendingUpdate, r1.id)
        rec.claimed_at = backdate
        s.add(rec)
        s.commit()

    moved = repo.mark_retryable()
    assert moved == 1
    assert repo.get_by_id(r1.id).status == PENDING_STATUS_RETRYABLE

    # Re-claim picks up the retryable row.
    claimed = repo.claim_batch("p1", run_token="run-B")
    assert len(claimed) == 1
    assert claimed[0].id == r1.id
    assert claimed[0].run_token == "run-B"
    assert claimed[0].retry_count == 1  # incremented on this re-claim


def test_mark_retryable_abandons_at_max_retries(repo, engine):
    """A row whose ``retry_count`` already meets the cap is moved
    straight to ``abandoned`` instead of ``retryable``."""
    r1 = repo.enqueue("p1", "experience", {"text": "a"})
    repo.claim_batch("p1", run_token="run-A")

    # Manually push retry_count to MAX_RETRIES for the test.
    with Session(engine) as s:
        rec = s.get(BlueprintPendingUpdate, r1.id)
        rec.retry_count = 3
        rec.claimed_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        s.add(rec)
        s.commit()

    moved = repo.mark_retryable(max_retries=3)
    assert moved == 1
    assert repo.get_by_id(r1.id).status == PENDING_STATUS_ABANDONED


def test_mark_retryable_skips_unclaimed_rows(repo):
    """``mark_retryable`` only touches rows already in ``claimed``."""
    repo.enqueue("p1", "experience", {"text": "a"})
    moved = repo.mark_retryable()
    assert moved == 0
    assert repo.list_pending("p1")[0].status == PENDING_STATUS_AVAILABLE


# ── Pruning ───────────────────────────────────────────────────────


def test_prune_processed_hard_deletes_old_processed_rows(repo, engine):
    r1 = repo.enqueue("p1", "experience", {"text": "a"})
    repo.claim_batch("p1", run_token="run-A")
    repo.acknowledge_batch("run-A", [r1.id])

    # Backdate processed_at to 8 days ago.
    backdate = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    with Session(engine) as s:
        rec = s.get(BlueprintPendingUpdate, r1.id)
        rec.processed_at = backdate
        s.add(rec)
        s.commit()

    deleted = repo.prune_processed("p1", older_than_days=7)
    assert deleted == 1
    assert repo.get_by_id(r1.id) is None


def test_prune_processed_keeps_recent_processed_rows(repo):
    r1 = repo.enqueue("p1", "experience", {"text": "a"})
    repo.claim_batch("p1", run_token="run-A")
    repo.acknowledge_batch("run-A", [r1.id])
    # processed_at is "now" → 7-day threshold kept it.
    deleted = repo.prune_processed("p1", older_than_days=7)
    assert deleted == 0
    assert repo.get_by_id(r1.id) is not None


def test_prune_excess_caps_per_project_unprocessed(repo):
    # Enqueue 5 rows.
    rows = [
        repo.enqueue("p1", "experience", {"text": f"row-{i}"})
        for i in range(5)
    ]
    # Cap to 2 → 3 oldest are deleted.
    deleted = repo.prune_excess("p1", max_records=2)
    assert deleted == 3
    remaining = repo.list_pending("p1")
    assert len(remaining) == 2
    # The kept rows are the two newest.
    assert {r.id for r in remaining} == {rows[3].id, rows[4].id}


def test_prune_excess_no_op_when_under_cap(repo):
    for i in range(3):
        repo.enqueue("p1", "experience", {"text": f"row-{i}"})
    deleted = repo.prune_excess("p1", max_records=10)
    assert deleted == 0
    assert repo.get_pending_count("p1") == 3


def test_prune_excess_does_not_touch_claimed_or_applied(repo):
    """Even when the cap is exceeded, claimed/applied/abandoned rows
    are not part of the FIFO cut."""
    claimed_row = repo.enqueue("p1", "experience", {"text": "claimed"})
    repo.claim_batch("p1", run_token="run-A", batch_size=1)
    # Re-read from the DB because claim_batch mutates rows in its own
    # Session — the in-memory object returned by ``enqueue`` is stale.
    assert repo.get_by_id(claimed_row.id).status == PENDING_STATUS_CLAIMED

    # Enqueue 5 more available rows.
    for i in range(5):
        repo.enqueue("p1", "experience", {"text": f"row-{i}"})

    # Cap to 2 → the available rows are pruned down to 2 (5 → 2,
    # so 3 deleted). The claimed row is untouched (CLAIMED is not
    # in the active set the prune considers).
    deleted = repo.prune_excess("p1", max_records=2)
    assert deleted == 3
    # The claimed row stays.
    assert repo.get_by_id(claimed_row.id) is not None
    assert repo.get_by_id(claimed_row.id).status == PENDING_STATUS_CLAIMED
    # The remaining available rows are the 2 newest.
    remaining = repo.list_pending("p1")
    assert len(remaining) == 2
