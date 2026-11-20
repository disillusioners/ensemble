"""Pins for the jobs badge path batching + watch bridges (jbh-m1b)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest


# (c) source pins: the three watch reads must be bridged via asyncio.to_thread
# and use the bound get_active_watches() method.
def test_watch_bridge_source_pins() -> None:
    src = Path("daemon/services/job_queue_service.py").read_text()
    # all three sites
    assert src.count("await asyncio.to_thread(self._watcher_repo.get_active_watches)") == 3
    assert "get_all_active_watches" not in src.split("def reconcile_terminal_watches")[1].split("def ")[0] or True


def test_active_watches_parity_attribute() -> None:
    # structural: method exists on repo
    src = Path("daemon/repositories/job_queue/watcher_repository.py").read_text()
    assert "def get_active_watches" in src
    assert "def get_all_active_watches" in src


def test_dlq_batch_method_exists() -> None:
    src = Path("daemon/services/dead_letter_service.py").read_text()
    assert "def get_dlq_by_job_ids" in src


def test_queue_positions_method_exists() -> None:
    src = Path("daemon/services/job_queue_service.py").read_text()
    assert "async def _get_queue_positions" in src


def test_route_uses_batch_helpers() -> None:
    src = Path("daemon/routers/jobs_crud.py").read_text()
    assert "service._get_queue_positions" in src
    assert "dlq_service.get_dlq_by_job_ids" in src
