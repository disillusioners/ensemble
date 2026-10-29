"""Unit tests for the checkpoint version census (plan Track-1 item 4).

The census answers the retention-pressure question the maintenance LOG
cannot answer reliably: the maintenance cycle's own "skipping
checkpoint_cleanup: system not idle" line is ``logger.DEBUG`` — invisible
at prod INFO — so a grep-based starvation audit is a FALSE-NEGATIVE
channel (the Phase-2 audit's finding). The census therefore lands in
``maintenance_runs.summary_json``, the reliable channel.

WHAT THE CENSUS REPORTS (and the honesty of each number):

* ``max_versions`` — the worst ``(thread_id, checkpoint_ns)`` pair's
  version count, PRE-prune (the pressure the cycle relieves). Derived
  from the prune pass' OWN ``find_all_thread_ns_pairs`` GROUP BY — no
  second scan.
* ``excess_pairs`` — the prune pass' own count of pairs over the
  retention cap.
* ``blob_bytes_by_channel`` — the POST-prune per-channel storage mass
  (one cheap aggregate on the dedicated maintenance engine).

Coverage:
    * the census block rides the run summary (``summary_json``);
    * the version half reuses the prune pass' scan (no second scan);
    * the blob half is recorded, and its skip is reported (an absent
      breakdown must never read as "zero bytes").
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from daemon.config import PersistenceConfig
from daemon.services.checkpoint_prune import BlobBytesCensus
from daemon.services.maintenance import CheckpointCleanupJob


def _adapter(*, pairs=None, excess=None) -> MagicMock:
    """A mock checkpointer — the prune pass' two read queries + the deletes."""
    adapter = MagicMock()
    adapter.list_thread_ids = AsyncMock(return_value=[])
    adapter.find_all_thread_ns_pairs = AsyncMock(
        return_value=pairs if pairs is not None else []
    )
    adapter.find_excess_checkpoint_groups = AsyncMock(
        return_value=excess if excess is not None else []
    )
    adapter.get_checkpoint_ids = AsyncMock(return_value=["ck-keep"])
    adapter.delete_checkpoints_excluding = AsyncMock(return_value=2)
    adapter.delete_writes_excluding = AsyncMock(return_value=5)
    adapter.count_writes_excluding = AsyncMock(return_value=1)
    return adapter


def _job(adapter: MagicMock) -> CheckpointCleanupJob:
    return CheckpointCleanupJob(
        config=PersistenceConfig(),
        checkpointer=adapter,
        instance_repo=MagicMock(),
    )


_BLOB_CENSUS = BlobBytesCensus(
    ran=True,
    rows=42,
    total_bytes=4096,
    by_channel={"messages": 4000, "": 96},
)


class TestVersionCensus:
    """The census rides the run summary."""

    @pytest.mark.asyncio
    async def test_census_lands_in_the_summary(self):
        adapter = _adapter(
            pairs=[("t-1", "", 546), ("t-2", "", 3)],
            excess=[("t-1", "", 546)],
        )
        job = _job(adapter)
        with patch(
            "daemon.services.maintenance.collect_blob_bytes_by_channel",
            AsyncMock(return_value=_BLOB_CENSUS),
        ):
            result = await job.run_checkpoint_prunes(destructive=True)

        summary = result.to_summary_dict()
        assert "version_census" in summary
        census = summary["version_census"]
        # F2: the census block's KEY SET is pinned EXACTLY — the keys
        # are the contract the audit-row consumers read. A dropped or
        # renamed key fails here (the previous pin only checked a few
        # VALUES, so a census that stopped reporting half its fields
        # stayed green).
        assert set(census.keys()) == {
            "ran",
            "blob_skipped_reason",
            "backend",
            "max_versions",
            "excess_pairs",
            "version_threshold",
            "scanned_pairs",
            "blob_rows",
            "blob_bytes_total",
            "blob_bytes_by_channel",
            "captured_at",
        }
        # The version half — the worst pair's version count (PRE-prune).
        assert census["max_versions"] == 546
        # The prune pass' own excess count (pairs over the cap).
        assert census["excess_pairs"] == 1
        assert census["scanned_pairs"] == 2
        assert census["version_threshold"] == 3  # the shipped keep-3 policy
        # The blob half — the POST-prune per-channel storage mass.
        assert census["blob_bytes_by_channel"] == {"messages": 4000, "": 96}
        assert census["blob_bytes_total"] == 4096
        assert census["blob_rows"] == 42
        assert census["ran"] is True
        assert census["blob_skipped_reason"] is None
        assert census["captured_at"]

    @pytest.mark.asyncio
    async def test_version_half_reuses_the_prune_scan(self):
        """No SECOND scan: the census reads the prune pass' own GROUP BY."""
        adapter = _adapter(pairs=[("t-1", "", 7)], excess=[])
        job = _job(adapter)
        with patch(
            "daemon.services.maintenance.collect_blob_bytes_by_channel",
            AsyncMock(return_value=BlobBytesCensus()),
        ):
            result = await job.run_checkpoint_prunes(destructive=True)
        # Exactly ONE pair scan for the whole cycle.
        assert adapter.find_all_thread_ns_pairs.await_count == 1
        # F2: the scan pin is TIED TO THE CENSUS — the scan count alone
        # constrains nothing (the scan pre-exists the census), so the
        # census must report the scan's OWN numbers. Deleting the census
        # (or its version half) now fails HERE, not only in the sibling
        # test.
        census = result.to_summary_dict()["version_census"]
        assert census["scanned_pairs"] == 1
        assert census["max_versions"] == 7
        assert census["excess_pairs"] == 0

    @pytest.mark.asyncio
    async def test_blob_skip_is_reported_not_hidden(self):
        """A skipped blob breakdown says so — it never reads as "zero bytes"."""
        adapter = _adapter(pairs=[("t-1", "", 4)], excess=[])
        job = _job(adapter)
        with patch(
            "daemon.services.maintenance.collect_blob_bytes_by_channel",
            AsyncMock(
                return_value=BlobBytesCensus(
                    ran=False, skipped_reason="no_shared_engine"
                )
            ),
        ):
            result = await job.run_checkpoint_prunes(destructive=True)

        census = result.to_summary_dict()["version_census"]
        assert census["blob_skipped_reason"] == "no_shared_engine"
        assert census["blob_bytes_by_channel"] == {}
        # The version half is still there (it needs no engine).
        assert census["max_versions"] == 4

    @pytest.mark.asyncio
    async def test_a_census_failure_never_fails_the_cycle(self):
        """A broken census must not break the cleanup cycle."""
        adapter = _adapter(pairs=[("t-1", "", 4)], excess=[])
        job = _job(adapter)
        with patch(
            "daemon.services.maintenance.collect_blob_bytes_by_channel",
            AsyncMock(side_effect=RuntimeError("census exploded")),
        ):
            result = await job.run_checkpoint_prunes(destructive=True)
        # The cycle completed; the version half survived and the blob
        # half says WHY it is missing (an absent breakdown must never
        # read as "zero bytes").
        assert result.rows.scanned_pairs == 1
        census = result.to_summary_dict()["version_census"]
        assert census["max_versions"] == 4
        assert census["blob_skipped_reason"] == "census_failed"
        assert census["blob_bytes_by_channel"] == {}

    def test_summary_without_a_census_keeps_the_frozen_shape(self):
        """No census ⇒ the frozen 4-key shape (an absent census is not a
        zero census)."""
        from daemon.services.maintenance import CheckpointRunResult

        summary = CheckpointRunResult().to_summary_dict()
        assert set(summary.keys()) == {
            "checkpoint_rows",
            "writes",
            "blobs",
            "duration_ms",
        }
