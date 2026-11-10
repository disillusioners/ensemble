"""Shared fixtures for the decision-module unit tests."""

from __future__ import annotations

import asyncio

import pytest

from daemon.services import db_offload


@pytest.fixture(autouse=True)
async def _setup_db_offload_loop():
    """Bind the db_offload seam to the CURRENT test loop.

    ``db_offload`` caches its loop; a test session that runs several
    event loops (one per test) needs the seam re-bound, or a worker
    future lands on a stale loop.
    """
    db_offload.configure(loop=asyncio.get_running_loop(), max_workers=4)
    yield
