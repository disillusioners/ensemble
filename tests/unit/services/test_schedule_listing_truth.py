"""PIN — the schedule listing tells the TRUTH (schedule listing truth, Phase 1).

Architecture v3 §3.3 "the listing floor" (non-negotiable, on EVERY surface):

1. recurrence truth — a recurring chain task NEVER renders as "once";
2. window — when the row has one (start / end / weekday / timezone);
3. ACTUAL next-fire from ONE authoritative source (the stored config) —
   non-null for an armed recurring row, identical on the tool and the
   REST surfaces;
4. last-fire + status;
5. project scope — the row's ``project_id`` is visible.

This file pins the TOOL surface (the shared service).  The REST surface
is pinned in ``tests/test_scheduler_api.py`` (same contract, REST
shape) — both surfaces derive through the SAME functions
(``listing_view`` + ``compute_next_run``), so one derivation mutation
breaks both.

MUTATION-VERIFIED (skill ``mutation-verified-regression-pins``): this
pin was proven to bite in a throwaway worktree — the derivation
mutation, the window-echo removal and the adapter-only next-fire
mutation each turn it RED, and each restore turns it GREEN again.

SAFETY FENCE: pure unit tests — in-memory doubles only.  No DB, no
daemon boot, no live contact.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from daemon.services import scheduling_service as svc


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------


def _row(
    source_id: str,
    *,
    name: str | None = None,
    config: dict | None = None,
    status: str = "running",
    enabled: bool = True,
    source_type: str = "scheduler",
) -> SimpleNamespace:
    return SimpleNamespace(
        source_id=source_id,
        source_type=source_type,
        name=name if name is not None else source_id,
        config=config or {},
        credentials=None,
        enabled=enabled,
        autostart=True,
        status=status,
        error_message=None,
        created_at="2026-11-01T00:00:00+00:00",
        updated_at="2026-11-01T00:00:00+00:00",
    )


class _FakeRepo:
    """In-memory source repository double with PAGE-AWARE listing.

    ``list_source_configs`` honours the (limit, offset) contract — the
    namespace scan pages through the rows, so a double that ignored the
    limit would loop forever (or hide the second live row).
    """

    def __init__(self) -> None:
        self.rows: dict[str, SimpleNamespace] = {}

    def add(self, row: SimpleNamespace) -> SimpleNamespace:
        self.rows[row.source_id] = row
        return row

    # -- reads --------------------------------------------------------------
    def get_source_config(self, source_id):
        return self.rows.get(source_id)

    def get_source_config_by_name(self, name):
        for row in self.rows.values():
            if row.name == name:
                return row
        return None

    def list_source_configs(self, enabled=None, status=None, limit=100, offset=0):
        rows = list(self.rows.values())
        if status is not None:
            rows = [r for r in rows if r.status == status]
        if enabled is not None:
            rows = [r for r in rows if r.enabled == enabled]
        return rows[offset : offset + limit]

    def get_latest_execution(self, schedule_id):
        return None

    # -- writes -------------------------------------------------------------
    def create_source_config(self, source_type, name, config, credentials=None,
                             enabled=True, autostart=True, source_id=None):
        source_id = source_id or name
        row = _row(source_id, name=name, config=config, status="stopped",
                   enabled=enabled, source_type=source_type)
        return self.add(row)


def _chain_config(run_at: datetime, **overrides) -> dict:
    """A chain-shaped (windowed) row config — the shape the maintenance
    seeder writes: ``recurrence="once"`` + a ``chain`` block."""
    config = {
        "agent": "maintenancer",
        "message": "weekly run",
        "timezone": "Asia/Ho_Chi_Minh",
        "recurrence": "once",
        "local_time": "03:30",
        "run_at": run_at.isoformat(),
        "instance_mode": "new_instance",
        "project_id": "agents-ensemble",
        "chain": {
            "mode": "weekly-random",
            "window_start": "01:00",
            "window_end": "05:00",
            "weekday": 0,
            "timezone": "Asia/Ho_Chi_Minh",
            "run_at_utc": run_at.isoformat(),
        },
    }
    config.update(overrides)
    return config


@pytest.fixture
def listing_env(monkeypatch):
    """A configured service with ONE chain-shaped row."""
    repo = _FakeRepo()
    run_at = datetime.now(timezone.utc) + timedelta(days=3)
    repo.add(_row("chain-1", name="central-maintenance-weekly",
                  config=_chain_config(run_at)))
    registry = MagicMock()
    registry.get = MagicMock(return_value=None)
    svc.configure(source_repo=repo, source_registry=registry)
    monkeypatch.setattr(svc, "_source_locks", {})
    yield repo, run_at
    svc._deps.source_repo = None
    svc._deps.source_registry = None
    svc._deps.project_repository = None
    svc._source_locks.clear()


# ---------------------------------------------------------------------------
# The pin
# ---------------------------------------------------------------------------


class TestListingTruth:
    @pytest.mark.asyncio
    async def test_a_chain_row_renders_derived_recurrence_and_window(self, listing_env):
        """1 + 2 — the effective recurrence is DERIVED and the window is
        echoed; the stored "once" is never the only truth."""
        repo, _run_at = listing_env
        items = await svc.list_schedules()
        assert len(items) == 1
        item = items[0]
        # 1. recurrence truth — NEVER "once" for a chain row.
        assert item.recurrence != "once"
        assert item.recurrence == svc.DERIVED_CHAIN_RECURRENCE
        # 2. window — start / end / weekday / timezone.
        assert item.window is not None
        assert item.window.start == "01:00"
        assert item.window.end == "05:00"
        assert item.window.weekday == 0
        assert item.window.timezone == "Asia/Ho_Chi_Minh"

    @pytest.mark.asyncio
    async def test_the_next_fire_is_non_null_for_an_armed_recurring_row(self, listing_env):
        """3 — the next-fire comes from the STORED config and is non-null
        for an armed recurring row."""
        repo, run_at = listing_env
        items = await svc.list_schedules()
        item = items[0]
        assert item.next_run_at_utc is not None
        assert item.next_run_at_local is not None
        # The stored ``run_at`` IS the next fire for a chain row.
        assert item.next_run_at_utc.startswith(run_at.astimezone(timezone.utc).isoformat()[:16])

    @pytest.mark.asyncio
    async def test_the_project_scope_is_visible(self, listing_env):
        """5 — the listing shows the row's project scope."""
        repo, _run_at = listing_env
        items = await svc.list_schedules()
        assert items[0].project_id == "agents-ensemble"

    @pytest.mark.asyncio
    async def test_the_derivation_is_read_side_only(self, listing_env):
        """READ-SIDE ONLY — the derivation never rewrites the stored
        config (no migration, no write path)."""
        repo, _run_at = listing_env
        await svc.list_schedules()
        stored = repo.rows["chain-1"].config
        assert stored["recurrence"] == "once"
        assert stored["chain"]["mode"] == "weekly-random"

    @pytest.mark.asyncio
    async def test_a_plain_row_is_not_turned_into_a_windowed_one(self, listing_env):
        """A row WITHOUT a chain block keeps its stored recurrence and
        renders no window (the derivation never invents a window)."""
        repo, _run_at = listing_env
        run_at = datetime.now(timezone.utc) + timedelta(days=2)
        repo.add(
            _row(
                "plain-1",
                name="plain-cron",
                config={
                    "agent": "ari",
                    "message": "hi",
                    "timezone": "UTC",
                    "recurrence": "cron",
                    "schedule": "30 3 * * 0",
                    "instance_mode": "new_instance",
                    "project_id": "agents-ensemble",
                },
            )
        )
        items = {i.source_id: i for i in await svc.list_schedules()}
        assert items["plain-1"].recurrence == "cron"
        assert items["plain-1"].window is None
        assert items["plain-1"].next_run_at_utc is not None
