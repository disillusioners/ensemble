"""Integration test: cleanup field on ``GET /api/tmp_images`` (phase 3, Task 7).

Real router + real store + real ``TmpImageCleanupService`` on an
ephemeral app (phase-1 integration pattern — no full create_app).
Pins:

* before any sweep: ``last_sweep_at`` is null (counters zeroed);
* after one manual ``sweep_once()``: ``last_sweep_at`` set and
  ``last_sweep_deleted`` matches the actual reaped count;
* the ``cleanup`` block carries NO ``enabled`` key (architect
  amendment #15 — absence pinned to prevent regression);
* the endpoint degrades to ``cleanup: null`` when the service is
  not wired (phase-1-only boot shapes).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from daemon.routers import tmp_images as tmp_images_module
from daemon.services.tmp_image_cleanup_service import TmpImageCleanupService
from daemon.services.tmp_image_store import TmpImageStore


@pytest.fixture(autouse=True)
def _debug_listing_enabled(monkeypatch: pytest.MonkeyPatch):
    # The gated debug listing returns 404 unless the env flag is on.
    monkeypatch.setenv("ENSEMBLE_TMP_IMAGE_DEBUG_LISTING", "1")


@pytest.fixture
def store(tmp_path: Path) -> TmpImageStore:
    s = TmpImageStore(tmp_path, max_bytes=10 * 1024 * 1024)
    s.init()
    return s


def _build_app(store: TmpImageStore, cleanup: TmpImageCleanupService | None):
    app = FastAPI()
    app.state.tmp_image_store = store
    if cleanup is not None:
        app.state.tmp_image_cleanup = cleanup
    api_router = APIRouter(prefix="/api")
    api_router.include_router(tmp_images_module.router)
    app.include_router(api_router)
    return app


def _seed_old(store: TmpImageStore, image_id: str) -> None:
    store.save(image_id, b"x", "image/png")
    old_iso = (
        datetime.now(timezone.utc) - timedelta(days=40)
    ).isoformat()
    sidecar = store.dir / f"{image_id}.json"
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    meta["uploaded_at"] = old_iso
    sidecar.write_text(json.dumps(meta), encoding="utf-8")
    old_ts = time.time() - 40 * 86400
    os.utime(store.dir / image_id, (old_ts, old_ts))
    os.utime(sidecar, (old_ts, old_ts))


class TestHealthCleanupField:
    def test_before_any_sweep_last_sweep_at_null(self, store, tmp_path):
        cleanup = TmpImageCleanupService(store)
        client = TestClient(_build_app(store, cleanup))

        resp = client.get("/api/tmp_images")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        cleanup_block = body["cleanup"]
        assert cleanup_block is not None
        assert cleanup_block["last_sweep_at"] is None
        assert cleanup_block["last_sweep_deleted"] == 0
        assert cleanup_block["last_sweep_error"] is None
        assert cleanup_block["interval_seconds"] == 3600
        assert cleanup_block["retention_days"] == 30

    def test_after_manual_sweep_populated_and_counts_match(
        self, store
    ):
        _seed_old(store, "a" * 32)
        store.save("f" * 32, b"fresh", "image/png")  # stays

        cleanup = TmpImageCleanupService(store)
        client = TestClient(_build_app(store, cleanup))

        # Health BEFORE the sweep.
        before = client.get("/api/tmp_images").json()["cleanup"]
        assert before["last_sweep_at"] is None

        # Manual deterministic tick (the documented self-test seam).
        reaped = asyncio.run(cleanup.sweep_once())
        assert reaped == 1

        after = client.get("/api/tmp_images").json()
        cleanup_block = after["cleanup"]
        assert cleanup_block["last_sweep_at"] is not None
        assert cleanup_block["last_sweep_deleted"] == reaped == 1
        assert cleanup_block["last_sweep_error"] is None
        assert after["count"] == 1, "only the fresh upload remains"

    def test_no_enabled_key_anywhere(self, store):
        cleanup = TmpImageCleanupService(store)
        client = TestClient(_build_app(store, cleanup))
        body = client.get("/api/tmp_images").json()
        assert "enabled" not in body["cleanup"], (
            "the cleanup block must NOT carry an 'enabled' key "
            "(architect amendment #15 — the sweep is always-on)"
        )
        # And the schema itself cannot grow one silently.
        from daemon.models.tmp_image import TmpImageCleanupStatus

        assert "enabled" not in TmpImageCleanupStatus.model_fields

    def test_cleanup_null_when_service_not_wired(self, store):
        client = TestClient(_build_app(store, cleanup=None))
        body = client.get("/api/tmp_images").json()
        assert body["cleanup"] is None
