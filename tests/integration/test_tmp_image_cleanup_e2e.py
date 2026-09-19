"""Integration e2e: upload → backdate → sweep → reap; O6 DELETE ∥ sweep race.

Phase 3 / clipboard-image-chat. Round-trip over the REAL router +
store + cleanup service on an ephemeral app (phase-1 integration
pattern):

* upload via the phase-1 ``POST /api/tmp_images`` → backdate the
  stored pair via ``os.utime(path, (t-31d, t-31d))`` →
  ``sweep_once()`` → files gone, ``GET`` returns 404;
* a FRESH upload survives the sweep (retention is age-based);
* **O6 race** — the FE ``DELETE`` endpoint raced by the sweep: when
  the sweep wins (file already gone before the endpoint's unlink),
  the endpoint still returns the idempotent 204, NEVER 500 (architect
  §7: FileNotFoundError swallowed silently = expected traffic). The
  inverse order (sweep after the FE delete) reaps nothing and stays
  error-free.
"""

from __future__ import annotations

import base64
import asyncio
import os
import time
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from daemon.routers import tmp_images as tmp_images_module
from daemon.services.tmp_image_cleanup_service import TmpImageCleanupService
from daemon.services.tmp_image_store import TmpImageStore

VALID_1X1_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "AAIAAAoAAv/lxKUAAAAASUVORK5CYII="
)


@pytest.fixture
def store(tmp_path: Path) -> TmpImageStore:
    s = TmpImageStore(tmp_path, max_bytes=10 * 1024 * 1024)
    s.init()
    return s


@pytest.fixture
def cleanup(store: TmpImageStore) -> TmpImageCleanupService:
    return TmpImageCleanupService(store)


@pytest.fixture
def client(store: TmpImageStore, cleanup: TmpImageCleanupService) -> TestClient:
    app = FastAPI()
    app.state.tmp_image_store = store
    app.state.tmp_image_cleanup = cleanup
    api_router = APIRouter(prefix="/api")
    api_router.include_router(tmp_images_module.router)
    app.include_router(api_router)
    return TestClient(app)


def _upload(client: TestClient, filename: str = "a.png") -> dict:
    resp = client.post(
        "/api/tmp_images",
        json={
            "images": [
                {
                    "filename": filename,
                    "content_type": "image/png",
                    "data_base64": VALID_1X1_PNG_B64,
                }
            ]
        },
    )
    assert resp.status_code == 200
    return resp.json()["uploads"][0]


def _backdate(store: TmpImageStore, image_id: str, days: float) -> None:
    """Age a stored pair to ``days`` old — the REAL-world aged shape.

    A genuinely 31-day-old upload carries an old sidecar
    ``uploaded_at`` (the sweep's age source per the ⚠ refinement)
    AND old mtimes. Both are backdated here; the mtime-only variant
    is pinned separately (sidecar precedence retains it).
    """
    import json
    from datetime import datetime, timedelta, timezone

    ts = time.time() - days * 86400
    os.utime(store.dir / image_id, (ts, ts))
    os.utime(store.dir / f"{image_id}.json", (ts, ts))
    sidecar = store.dir / f"{image_id}.json"
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    meta["uploaded_at"] = (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).isoformat()
    sidecar.write_text(json.dumps(meta), encoding="utf-8")


class TestUploadBackdateSweepReap:
    def test_backdated_upload_reaped_then_get_404(
        self, store: TmpImageStore, cleanup: TmpImageCleanupService, client
    ):
        upload = _upload(client)
        image_id = upload["image_id"]
        ref_url = upload["ref_url"]

        # Sanity: served before the sweep.
        assert client.get(ref_url).status_code == 200

        _backdate(store, image_id, days=31)
        reaped = asyncio.run(cleanup.sweep_once())
        assert reaped == 1

        assert not (store.dir / image_id).exists()
        assert not (store.dir / f"{image_id}.json").exists()
        resp = client.get(ref_url)
        assert resp.status_code == 404

    def test_mtime_only_backdate_retained_sidecar_precedence(
        self, store: TmpImageStore, cleanup: TmpImageCleanupService, client
    ):
        # Integration-level pin of the ⚠ refinement: a file whose
        # MTIME is ancient but whose sidecar ``uploaded_at`` is fresh
        # (e.g. a restore/copy that reset mtimes) is RETAINED — the
        # sidecar is the age source when it parses.
        upload = _upload(client)
        image_id = upload["image_id"]
        ts = time.time() - 31 * 86400
        os.utime(store.dir / image_id, (ts, ts))
        os.utime(store.dir / f"{image_id}.json", (ts, ts))
        assert asyncio.run(cleanup.sweep_once()) == 0
        assert (store.dir / image_id).exists()

    def test_fresh_upload_survives_sweep(
        self, store: TmpImageStore, cleanup: TmpImageCleanupService, client
    ):
        upload = _upload(client)
        ref_url = upload["ref_url"]
        reaped = asyncio.run(cleanup.sweep_once())
        assert reaped == 0
        assert client.get(ref_url).status_code == 200
        assert (store.dir / upload["image_id"]).exists()

    def test_sweep_uploads_state_matches_wire_count(
        self, store: TmpImageStore, cleanup: TmpImageCleanupService, client
    ):
        ids = [_upload(client, f"{n}.png")["image_id"] for n in range(3)]
        for image_id in ids[:2]:
            _backdate(store, image_id, days=45)
        reaped = asyncio.run(cleanup.sweep_once())
        assert reaped == 2
        assert cleanup.last_sweep_deleted == 2
        assert store.count() == 1
        assert cleanup.last_sweep_at is not None


class TestO6DeleteSweepRace:
    def test_sweep_wins_delete_endpoint_still_204_never_500(
        self, store: TmpImageStore, cleanup: TmpImageCleanupService, client
    ):
        # The sweep deletes the files BETWEEN the FE's request and the
        # endpoint's unlink. Deterministic simulation: reap first,
        # then issue the FE DELETE — the endpoint must treat the
        # missing files as idempotent success (204), never 500.
        upload = _upload(client)
        image_id = upload["image_id"]
        assert asyncio.run(cleanup.sweep_once()) == 0  # not old yet
        _backdate(store, image_id, days=31)
        assert asyncio.run(cleanup.sweep_once()) == 1

        resp = client.delete(f"/api/tmp_images/{image_id}")
        assert resp.status_code == 204, (
            "DELETE raced by the sweep returns the idempotent 204 "
            "(FileNotFoundError swallowed per architect §7), never 500"
        )

    def test_sweep_after_delete_reaps_nothing_and_no_error(
        self, store: TmpImageStore, cleanup: TmpImageCleanupService, client
    ):
        # Inverse order: the FE DELETE wins, the sweep's next tick
        # sees nothing to reap for that id and reports no error.
        upload = _upload(client)
        image_id = upload["image_id"]
        assert client.delete(f"/api/tmp_images/{image_id}").status_code == 204
        assert asyncio.run(cleanup.sweep_once()) == 0
        assert cleanup.last_sweep_error is None

    def test_repeated_delete_and_sweep_stays_idempotent(
        self, store: TmpImageStore, cleanup: TmpImageCleanupService, client
    ):
        # The full interleaving — delete, delete, sweep, delete —
        # every call succeeds; nothing 500s.
        upload = _upload(client)
        image_id = upload["image_id"]
        _backdate(store, image_id, days=31)
        assert client.delete(f"/api/tmp_images/{image_id}").status_code == 204
        assert client.delete(f"/api/tmp_images/{image_id}").status_code == 204
        assert asyncio.run(cleanup.sweep_once()) == 0
        assert client.delete(f"/api/tmp_images/{image_id}").status_code == 204
        assert cleanup.last_sweep_error is None
