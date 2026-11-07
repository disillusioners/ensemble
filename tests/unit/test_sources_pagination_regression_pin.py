"""Regression pin pack — wi-b97ec302 sources pagination (data-growth latent defect).

ROOT CAUSE (CONFIRMED by the diagnosis phase + tester both-direction repro —
do not re-litigate here): ``SQLModelSourceRepository.list_source_configs``
defaults ``limit=100`` with an unconditional ``ORDER BY created_at DESC
LIMIT 100``. The boot loader (``SourceRegistry.start_all``) and the LIST
surfaces called it with the default, so once more than 100 newer inert
one-shot scheduler rows existed (each re-arm writes a fresh row with a
newer ``created_at``; rows are never deleted), the oldest chat rows fell
off page 1: boot silently stopped auto-starting them and ``GET /api/sources``
/ ``GET /api/schedules`` hid them.

DISCOURSE RULE (leader instruction, binding on commits AND comments): this
is a DATA-GROWTH-TRIGGERED LATENT DEFECT — latent since the 2026-03
default-limit wiring (7c63bcb00e / 81b259fb79). It is NOT a v0.29.1-
introduced regression; the release window was exonerated by the tester's
same-data repro on the prior build (v0.29.0 fails identically).

LEG MAP
=======
(a) boot loader observes every row (past the old implicit 100-row cap) and
    schedules the oldest discord chat row; the repository paging contract
    itself is pinned (explicit limit=500 returns all, default returns 100).
(b) ``GET /api/sources``: default call returns EVERY row up to the hard max
    with the untruncated ``total``; explicit ``limit``/``offset`` page the
    same ordering; ``limit`` above the hard max CLAMPS (documented choice
    over 422 — ``total`` makes a clamp lossless for the caller);
    structurally invalid params are 422 via query validation.
(c) post-boot reconciliation: stale ``running`` rows this boot did NOT
    schedule/start flip to ``stopped``; rows scheduled by this boot and
    rows with already-registered adapters are spared (ordering is
    load-bearing — a pre-loop sweep would flip running→stopped and the
    per-row guard would then skip auto-start); a sweep failure never
    blocks boot.
(d) the same-seam completions: ``GET /api/schedules`` and
    ``scheduling_service.list_schedules`` observe every row (105 > the old
    implicit cap of 100 discriminates).

MUTATION LEG (executed by the LANE, not in-test — see the commit message
and the lane report): restore the old single default-limited call in
``SourceRegistry.start_all`` (``list_source_configs()`` with defaults) —
leg (a) goes RED (the discord row is evicted from page 1 by the 105 newer
tombstones). Both directions were run with digests recorded; the fix was
restored before commit.

SUBSTRATE / FENCES
==================
Hermetic file-backed SQLite under ``tmp_path`` (real engine, real rows,
``NullPool`` — never ``StaticPool``/``:memory:``). No daemon boot, no
PostgreSQL, no port binding, no network, no live contact, no agents/
writes, no migration writes. Fixture shape mirrors the tester's repro
(105 newer inert scheduler tombstones + 1 oldest discord chat row).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel

import daemon.repositories.source.models  # noqa: F401  (register source tables)
from daemon.constants import SOURCE_CONFIG_PAGE_SIZE, SOURCE_LIST_HARD_MAX
from daemon.repositories.source.models import SourceConfig as SourceConfigRow
from daemon.repositories.source.repository import SQLModelSourceRepository
from daemon.routers.sources import router as sources_router
from daemon.services import scheduling_service as svc
from daemon.sources.registry import SourceRegistry

# ---------------------------------------------------------------------------
# Fixture shape constants (mirror of the tester's repro data)
# ---------------------------------------------------------------------------

#: The OLDEST row — the legacy discord chat row that must never be evicted.
DISCORD_ID = "discord-legacy-chat"
#: Newest tombstone created_at day offset (tombstones are NEWER than discord).
TOMBSTONE_BASE_DAY = 200
#: Number of inert tombstone rows seeded ahead of the discord row.
TOMBSTONE_COUNT = 105
#: The disabled stale-running tombstones (reconciliation targets): indices.
STALE_RUNNING_INDICES = (24, 49, 74, 99)


def _created_at(days_from_base: int) -> str:
    """Uniform-format ISO timestamp — lexicographic == chronological."""
    moment = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=days_from_base)
    return moment.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _make_scratch_engine(path) -> Engine:
    """Hermetic file-backed SQLite engine (NullPool — probe lineage)."""
    return create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )


def _seed_tombstone(eng: Engine, index: int) -> str:
    """Seed one inert scheduler tombstone (the self-alarm re-arm shape).

    Mixed enabled true/false, mostly ``status='stopped'``; a few disabled
    rows carry a stale ``status='running'`` (the unclean-shutdown residue
    the reconciliation sweep exists for). Every tombstone is NEWER than
    the discord row.
    """
    if index in STALE_RUNNING_INDICES:
        enabled, status = False, "running"
    elif index % 2 == 0:
        enabled, status = False, "stopped"
    else:
        enabled, status = True, "stopped"
    source_id = f"scheduler-tomb-{index:03d}"
    with Session(eng) as session:
        session.add(
            SourceConfigRow(
                source_id=source_id,
                source_type="scheduler",
                name=f"self-alarm:tomb-{index:03d}",
                config={"kind": "one_shot", "armed_at": _created_at(TOMBSTONE_BASE_DAY + index)},
                credentials=None,
                enabled=enabled,
                autostart=True,
                status=status,
                error_message=None,
                created_at=_created_at(TOMBSTONE_BASE_DAY + index),
                updated_at=_created_at(TOMBSTONE_BASE_DAY + index),
            )
        )
        session.commit()
    return source_id


def _seed_discord_row(eng: Engine) -> str:
    """Seed the legacy discord chat row — OLDEST, enabled, autostart, running."""
    with Session(eng) as session:
        session.add(
            SourceConfigRow(
                source_id=DISCORD_ID,
                source_type="discord",
                name="my-discord-bot",
                config={"polling_enabled": True, "default_agent": "developer"},
                credentials='{"bot_token": "scratch-not-real"}',
                enabled=True,
                autostart=True,
                status="running",
                error_message=None,
                created_at=_created_at(0),
                updated_at=_created_at(0),
            )
        )
        session.commit()
    return DISCORD_ID


def _seed_webhook_row(eng: Engine, source_id: str) -> str:
    """Seed a disabled stale-running webhook row (registered-adapter spare leg)."""
    with Session(eng) as session:
        session.add(
            SourceConfigRow(
                source_id=source_id,
                source_type="webhook",
                name="webhook-stale",
                config={"secret": "x"},
                credentials=None,
                enabled=False,
                autostart=True,
                status="running",
                error_message=None,
                created_at=_created_at(TOMBSTONE_BASE_DAY + TOMBSTONE_COUNT),
                updated_at=_created_at(TOMBSTONE_BASE_DAY + TOMBSTONE_COUNT),
            )
        )
        session.commit()
    return source_id


@pytest.fixture
def sqlite_engine(tmp_path):
    """Hermetic file-backed SQLite engine (real schema via create_all)."""
    eng = _make_scratch_engine(tmp_path / "sources_pagination_pin.db")
    SQLModel.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def source_repo(sqlite_engine) -> SQLModelSourceRepository:
    """The REAL source repository over the scratch engine."""
    return SQLModelSourceRepository(sqlite_engine)


@pytest.fixture
def repro_db(source_repo):
    """Seed the tester-repro shape: 105 newer tombstones + the oldest discord row."""
    eng = source_repo.engine
    for i in range(TOMBSTONE_COUNT):
        _seed_tombstone(eng, i)
    _seed_discord_row(eng)
    return source_repo


def _make_registry(repo) -> SourceRegistry:
    """A SourceRegistry wired to the real repo; manager untouched by start_all."""
    return SourceRegistry(repo, MagicMock())


def _patch_boot(recorded: list[str], registry: SourceRegistry) -> None:
    """Make the boot loop hermetic: fake adapter creation + autostart recorder.

    The recorder is SYNC on purpose — the production ``_schedule_autostart``
    is sync (it creates the delayed task internally); a coroutine recorder
    would never be awaited and silently drop the signal.
    """
    registry._create_adapter_from_config = AsyncMock(
        return_value=MagicMock(source_id=DISCORD_ID, source_type="discord")
    )

    def _recorder(source_id: str) -> None:
        recorded.append(source_id)

    registry._schedule_autostart = _recorder


def _sources_app(repo: SQLModelSourceRepository) -> FastAPI:
    """Minimal app mounting ONLY the sources router over the real repo."""
    app = FastAPI()
    app.include_router(sources_router, prefix="/api")
    app.state.manager = SimpleNamespace(_source_repository=repo)
    return app


# ---------------------------------------------------------------------------
# (a) Boot loader observes every row
# ---------------------------------------------------------------------------


class TestBootLoaderPaging:
    """start_all must observe rows beyond the old implicit 100-row page."""

    @pytest.mark.asyncio
    async def test_boot_loader_schedules_oldest_discord_row(self, repro_db):
        """(a) The oldest discord row is seen + scheduled despite 105 newer rows."""
        registry = _make_registry(repro_db)
        recorded: list[str] = []
        _patch_boot(recorded, registry)

        await registry.start_all()

        # The discord row passed the guards (enabled + autostart + running)
        # and was scheduled — page-1 eviction would drop it entirely.
        assert recorded == [DISCORD_ID]
        assert DISCORD_ID in registry._adapters

        # Sweep ran: the disabled stale-running tombstones reconciled to
        # stopped (leg (c) rides the same boot call — asserted in detail
        # by TestStaleRunningReconciliation; here just the discriminator).
        stale = repro_db.get_source_config(f"scheduler-tomb-{STALE_RUNNING_INDICES[0]:03d}")
        assert stale is not None and stale.status == "stopped"

    def test_repo_paging_contract_explicit_limit_returns_all(self, repro_db):
        """(a) The repository paging contract: explicit big limit sees every row."""
        rows = repro_db.list_source_configs(None, None, SOURCE_CONFIG_PAGE_SIZE, 0)
        assert len(rows) == TOMBSTONE_COUNT + 1
        assert {r.source_id for r in rows} >= {DISCORD_ID}

    def test_repo_default_call_is_one_capped_page(self, repro_db):
        """(a) The DEFAULT call is a capped page — the defect shape, pinned so
        the contract stays explicit: default = page, NOT completeness."""
        rows = repro_db.list_source_configs()
        assert len(rows) == 100  # the historical page cap
        assert DISCORD_ID not in {r.source_id for r in rows}  # oldest evicted


# ---------------------------------------------------------------------------
# (b) GET /api/sources — real pagination + total + clamp
# ---------------------------------------------------------------------------


class TestSourcesListPagination:
    """The LIST endpoint must never silently hide rows."""

    @pytest.mark.asyncio
    async def test_default_returns_full_list_with_total(self, repro_db):
        """(b) Default call: every row up to the hard max + untruncated total."""
        app = _sources_app(repro_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t/api") as client:
            response = await client.get("/sources")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == TOMBSTONE_COUNT + 1
        assert len(data["sources"]) == TOMBSTONE_COUNT + 1
        ids = {s["source_id"] for s in data["sources"]}
        assert DISCORD_ID in ids

    @pytest.mark.asyncio
    async def test_explicit_pagination_pages_stable_ordering(self, repro_db):
        """(b) Explicit limit/offset page the created_at DESC ordering."""
        app = _sources_app(repro_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t/api") as client:
            page1 = await client.get("/sources", params={"limit": 10, "offset": 0})
            last_page = await client.get("/sources", params={"limit": 10, "offset": 105})
        assert page1.status_code == 200 and last_page.status_code == 200
        p1, pl = page1.json(), last_page.json()
        assert p1["total"] == TOMBSTONE_COUNT + 1
        assert len(p1["sources"]) == 10
        # Newest-first: page 1 must NOT contain the oldest discord row.
        assert DISCORD_ID not in {s["source_id"] for s in p1["sources"]}
        # The tail page holds exactly the oldest row.
        assert pl["total"] == TOMBSTONE_COUNT + 1
        assert [s["source_id"] for s in pl["sources"]] == [DISCORD_ID]

    @pytest.mark.asyncio
    async def test_limit_above_hard_max_clamps(self, tmp_path):
        """(b) limit > SOURCE_LIST_HARD_MAX is CLAMPED (documented choice).

        Seeded beyond the hard max so the clamp is behaviorally visible:
        a 5000-limit request returns exactly HARD_MAX rows with the true
        total — the caller can always detect truncation via total.
        """
        db_path = tmp_path / "sources_clamp_pin.db"
        eng = _make_scratch_engine(db_path)
        SQLModel.metadata.create_all(eng)
        try:
            over = SOURCE_LIST_HARD_MAX + 5
            with Session(eng) as session:
                for i in range(over):
                    session.add(
                        SourceConfigRow(
                            source_id=f"clamp-{i:04d}",
                            source_type="scheduler",
                            name=f"clamp-{i:04d}",
                            config={},
                            credentials=None,
                            enabled=False,
                            autostart=False,
                            status="stopped",
                            error_message=None,
                            created_at=_created_at(TOMBSTONE_BASE_DAY + i),
                            updated_at=_created_at(TOMBSTONE_BASE_DAY + i),
                        )
                    )
                session.commit()
            repo = SQLModelSourceRepository(eng)
            app = _sources_app(repo)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t/api") as client:
                clamped = await client.get("/sources", params={"limit": 5000})
                default = await client.get("/sources")
            assert clamped.status_code == 200
            data = clamped.json()
            assert len(data["sources"]) == SOURCE_LIST_HARD_MAX
            assert data["total"] == over
            assert default.status_code == 200
            assert len(default.json()["sources"]) == SOURCE_LIST_HARD_MAX
            assert default.json()["total"] == over
        finally:
            eng.dispose()

    @pytest.mark.asyncio
    async def test_structurally_invalid_pagination_is_422(self, repro_db):
        """(b) limit<1 / offset<0 are query-validation 422s (fail-closed shape)."""
        app = _sources_app(repro_db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t/api") as client:
            bad_limit = await client.get("/sources", params={"limit": 0})
            bad_offset = await client.get("/sources", params={"offset": -1})
        assert bad_limit.status_code == 422
        assert bad_offset.status_code == 422


# ---------------------------------------------------------------------------
# (c) Post-boot reconciliation of stale running rows
# ---------------------------------------------------------------------------


class TestStaleRunningReconciliation:
    """Boot reconciles rows it did not schedule; ordering is load-bearing."""

    @pytest.mark.asyncio
    async def test_stale_running_rows_flip_to_stopped(self, repro_db):
        """(c) Disabled stale-running rows (not scheduled) reconcile to stopped."""
        registry = _make_registry(repro_db)
        recorded: list[str] = []
        _patch_boot(recorded, registry)

        await registry.start_all()

        for index in STALE_RUNNING_INDICES:
            row = repro_db.get_source_config(f"scheduler-tomb-{index:03d}")
            assert row.status == "stopped", f"tomb-{index:03d} not reconciled"

        # Running rows left in the DB == exactly the protected discord row.
        still_running = repro_db.list_source_configs(None, "running", SOURCE_CONFIG_PAGE_SIZE, 0)
        assert [r.source_id for r in still_running] == [DISCORD_ID]

    @pytest.mark.asyncio
    async def test_scheduled_row_is_spared_running_status(self, repro_db):
        """(c) Ordering is load-bearing: the scheduled discord row keeps its
        running status — a pre-loop sweep would flip it and the per-row
        guard would then skip auto-start."""
        registry = _make_registry(repro_db)
        recorded: list[str] = []
        _patch_boot(recorded, registry)

        await registry.start_all()

        assert recorded == [DISCORD_ID]  # guard passed ⇒ row was seen as running
        discord_row = repro_db.get_source_config(DISCORD_ID)
        assert discord_row.status == "running"

    @pytest.mark.asyncio
    async def test_registered_adapter_row_is_spared(self, repro_db):
        """(c) A row whose adapter is already registered is spared even though
        this boot did not schedule it."""
        webhook_id = _seed_webhook_row(repro_db.engine, "webhook-stale-running")
        registry = _make_registry(repro_db)
        registry.register(MagicMock(source_id=webhook_id, source_type="webhook"))
        recorded: list[str] = []
        _patch_boot(recorded, registry)

        await registry.start_all()

        assert webhook_id not in recorded  # loader skipped it (disabled)
        row = repro_db.get_source_config(webhook_id)
        assert row.status == "running"  # spared: registered

    @pytest.mark.asyncio
    async def test_sweep_failure_never_blocks_boot(self, repro_db, monkeypatch):
        """(c) A reconciliation write failure logs and moves on — boot survives."""
        registry = _make_registry(repro_db)
        recorded: list[str] = []
        _patch_boot(recorded, registry)

        def _boom(*_args, **_kwargs):
            raise RuntimeError("scratch write failure")

        monkeypatch.setattr(repro_db, "update_source_status", _boom)

        # Must NOT raise.
        await registry.start_all()
        assert recorded == [DISCORD_ID]


# ---------------------------------------------------------------------------
# (d) Same-seam completions: /api/schedules + scheduling_service
# ---------------------------------------------------------------------------


@pytest.fixture
def service_deps(monkeypatch, source_repo):
    """Fresh module-level service deps (monkeypatch restores the singleton)."""
    fresh = svc._Deps()
    fresh.source_repo = source_repo
    fresh.source_registry = MagicMock()
    monkeypatch.setattr(svc, "_deps", fresh)
    return fresh


class TestSameSeamCompletion:
    """Schedules surfaces must observe every row (105 > old cap of 100)."""

    @pytest.mark.asyncio
    async def test_schedules_endpoint_returns_all_scheduler_rows(self, repro_db):
        """(d) GET /api/schedules sees all 105 tombstones (old cap hid 5)."""
        app = FastAPI()
        from daemon.routers.schedules import router as schedules_router

        app.include_router(schedules_router, prefix="/api")
        app.state.manager = SimpleNamespace(
            _source_repository=repro_db, source_registry=None
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t/api") as client:
            response = await client.get("/schedules")
        assert response.status_code == 200
        data = response.json()
        assert len(data["schedules"]) == TOMBSTONE_COUNT  # > 100 ⇒ paging happened
        ids = {s["id"] for s in data["schedules"]}
        assert DISCORD_ID not in ids  # scheduler-only filter intact
        assert f"scheduler-tomb-{TOMBSTONE_COUNT - 1:03d}" in ids  # newest seen

    @pytest.mark.asyncio
    async def test_scheduling_service_list_sees_all_rows(self, repro_db, service_deps):
        """(d) scheduling_service.list_schedules observes every row too."""
        items = await svc.list_schedules()
        assert len(items) == TOMBSTONE_COUNT
        assert all(item.source_id.startswith("scheduler-tomb-") for item in items)

    @pytest.mark.asyncio
    async def test_scheduling_service_status_filter_still_works(self, repro_db, service_deps):
        """(d) Pre-existing filters intact across the paged read."""
        items = await svc.list_schedules(status="stopped")
        expected = TOMBSTONE_COUNT - len(STALE_RUNNING_INDICES)
        assert len(items) == expected
