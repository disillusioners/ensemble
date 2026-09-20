"""Integration test: tmp-image cleanup service lifespan wiring (phase 3).

Approach (mirrors the phase-1 boot-test doctrine in
``tests/integration/test_tmp_images_boot.py``): the full
``daemon.api.create_app()`` lifespan is heavy (boots the manager, the
worker pool, every watchdog) and blocked by the unit-test conftest's
``langgraph.checkpoint`` stub. We exercise the SAME code path the
production lifespan runs — store build → cleanup construct from
``config.services`` → ``start()`` → ``app.state`` assignment → boot
log → shutdown mirror — against an ephemeral FastAPI app with a REAL
lifespan (``with TestClient(app)`` drives startup + shutdown), and
pin the production wiring shape with doc-truth greps of
``daemon/api.py`` (same style as
``tests/unit/job_queue/test_joblock_sweep_lifecycle.py``).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from daemon.config import ServicesConfig
from daemon.services.tmp_image_cleanup_service import TmpImageCleanupService
from daemon.services.tmp_image_store import build_tmp_image_store


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def app(data_dir: Path) -> FastAPI:
    """Ephemeral app whose lifespan MIRRORS the production wiring.

    Sequence parity with ``daemon/api.py`` (phase 3 Tasks 4-5):
    1. build the store (phase-1 factory seam);
    2. read interval + retention from the services config (the
       lifespan reads ``config.services.*`` off the root settings —
       here we instantiate ServicesConfig directly, the same object
       the root config exposes);
    3. construct the cleanup service with ``enabled=True`` HARDCODED;
    4. ``start()`` + assign ``app.state.tmp_image_cleanup``;
    5. boot log ``[TmpImages] cleanup service started: ...``;
    6. on shutdown: getattr-guarded ``stop()`` BEFORE the (absent
       here) JobLockSweepService shutdown, WARNING on failure,
       ``app.state`` cleared.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = ServicesConfig()
        store = build_tmp_image_store(
            data_dir=data_dir,
            max_bytes=config.tmp_image_store_max_bytes,
        )
        app.state.tmp_image_store = store
        logging.getLogger("daemon.api").info(
            f"[TmpImages] ready: dir={store.dir} "
            f"count={store.count()} max_bytes={store.max_bytes}"
        )

        cleanup = TmpImageCleanupService(
            store,
            enabled=True,
            interval_seconds=config.tmp_image_cleanup_interval_seconds,
            retention_days=config.tmp_image_cleanup_retention_days,
        )
        cleanup.start()
        app.state.tmp_image_cleanup = cleanup
        logging.getLogger("daemon.api").info(
            f"[TmpImages] cleanup service started: interval="
            f"{config.tmp_image_cleanup_interval_seconds}s "
            f"retention={config.tmp_image_cleanup_retention_days}d"
        )
        try:
            yield
        finally:
            service = getattr(app.state, "tmp_image_cleanup", None)
            if service is not None:
                try:
                    await service.stop()
                except Exception as e:  # noqa: BLE001 — mirror production
                    logging.getLogger("daemon.api").warning(
                        f"TmpImageCleanupService shutdown error: {e}"
                    )
                app.state.tmp_image_cleanup = None

    return FastAPI(lifespan=lifespan)


class TestLifespanBootAndShutdown:
    def test_task_alive_during_boot_done_after_shutdown(self, app, data_dir):
        with TestClient(app) as client:
            cleanup = app.state.tmp_image_cleanup
            assert cleanup is not None
            assert isinstance(cleanup, TmpImageCleanupService)
            assert cleanup._task is not None, "service task alive after boot"
            task = cleanup._task
            assert not task.done()
            # Config knobs flowed through from the services config.
            assert cleanup.interval_seconds == 3600
            assert cleanup.retention_days == 30
            # The store wiring (phase 1) is still intact.
            assert app.state.tmp_image_store.dir == data_dir / "tmp_images"
            assert client.get("/api/healthz").status_code in (200, 404)
        # After the lifespan exits: the shutdown mirror ran — stop()
        # awaited the task to completion and detached it.
        assert task.done()
        assert cleanup._task is None
        assert app.state.tmp_image_cleanup is None

    def test_boot_log_line_shape(self, app, caplog):
        with caplog.at_level(logging.INFO, logger="daemon.api"):
            with TestClient(app):
                pass
        anchors = [
            r
            for r in caplog.records
            if "[TmpImages] cleanup service started:" in r.getMessage()
        ]
        assert len(anchors) == 1
        msg = anchors[0].getMessage()
        assert "interval=3600s" in msg
        assert "retention=30d" in msg
        # NO enabled= field — the service is ALWAYS-ON (amendment #15).
        assert "enabled=" not in msg


class TestProductionWiringDocTruth:
    """Pin that ``daemon/api.py`` carries the real wiring literals.

    Same doc-truth style as ``test_joblock_sweep_lifecycle.py`` — the
    ephemeral app above proves the SEQUENCE works; these greps prove
    production actually wires it (the seam can drift silently
    otherwise).
    """

    def _api_src(self) -> str:
        api_path = (
            Path(__file__).resolve().parent.parent.parent
            / "daemon"
            / "api.py"
        )
        assert api_path.is_file()
        return api_path.read_text(encoding="utf-8")

    def test_boot_anchor_literals_present(self):
        src = self._api_src()
        assert "[TmpImages] cleanup service started: interval=" in src, (
            "daemon/api.py must contain the phase-3 boot log literal"
        )
        assert "config.services.tmp_image_cleanup_interval_seconds" in src
        assert "config.services.tmp_image_cleanup_retention_days" in src
        assert "app.state.tmp_image_cleanup = tmp_image_cleanup" in src
        assert "enabled=True" in src, (
            "the boot anchor must hardcode enabled=True (internal "
            "test seam, never a production toggle)"
        )

    def test_shutdown_mirror_ordered_before_job_lock_sweep(self):
        src = self._api_src()
        assert 'getattr(app.state, "tmp_image_cleanup", None)' in src
        assert "TmpImageCleanupService shutdown error" in src
        tmp_cleanup_idx = src.index(
            'getattr(app.state, "tmp_image_cleanup", None)'
        )
        job_lock_idx = src.index(
            'getattr(app.state, "job_lock_sweep", None)'
        )
        assert tmp_cleanup_idx < job_lock_idx, (
            "the cleanup shutdown MUST run BEFORE the JobLockSweep "
            "shutdown (cleanup stops touching the fs first)"
        )

    def test_boot_wiring_ordered_after_job_lock_sweep(self):
        src = self._api_src()
        boot_block_idx = src.index("TmpImageCleanupService(")
        job_lock_boot_idx = src.index(
            "JobLockSweepService started: interval="
        )
        assert boot_block_idx > job_lock_boot_idx, (
            "the cleanup boot block must come AFTER the "
            "JobLockSweepService boot block (plan Task 4)"
        )
