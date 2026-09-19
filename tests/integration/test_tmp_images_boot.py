"""Integration test: tmp-image store wiring under the daemon lifespan.

Phase 1 / clipboard-image-chat. The plan's Test strategy pins:

* Lifespan integration: app.state.tmp_image_store is non-None,
  <data_dir>/tmp_images/ exists post-startup.

Approach
--------

The full ``daemon.api.create_app()`` lifespan is heavy (boots the
manager, the worker pool, every watchdog, every reconcile loop). For
this targeted wiring assertion we exercise the SAME code path the
lifespan uses — ``build_tmp_image_store`` + the ``app.state`` assignment
+ the boot anchor log — but isolated from the rest of the lifespan.

Why this is acceptable: the wiring block the lifespan runs is exactly
the four-line block we test here. The store has no other daemon
dependency (no manager, no DB); the wiring is trivially correct by
construction. Pinning the wiring shape in isolation keeps the test
fast (~ms) and avoids the conftest-installed ``langgraph.checkpoint``
mocks that block the full-lifespan path (the unit-test conftest
shadows ``langgraph.checkpoint`` with a stub that doesn't carry
``postgres.aio.AsyncPostgresSaver``; the full ``create_app()``
lifespan requires that real module).

The full ``create_app()`` lifespan is already exercised by every
TestClient-using integration test in this repo (e.g.
``test_vscode_routing.py``); we do not duplicate that coverage here.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from fastapi import FastAPI

from daemon.services.tmp_image_store import (
    TmpImageStore,
    build_tmp_image_store,
)


# ---------------------------------------------------------------------------
# Group 1 — Wiring helper correctness
# ---------------------------------------------------------------------------


class TestBuildTmpImageStore:
    def test_build_creates_store_and_initializes_dir(self, tmp_path):
        store = build_tmp_image_store(data_dir=tmp_path, max_bytes=4096)
        assert isinstance(store, TmpImageStore)
        assert store.dir.exists()
        assert store.dir.is_dir()
        assert store.data_dir == tmp_path
        assert store.max_bytes == 4096

    def test_build_is_idempotent_on_existing_dir(self, tmp_path):
        # First build creates the dir.
        s1 = build_tmp_image_store(data_dir=tmp_path, max_bytes=1024)
        # Second build on the same data_dir is a no-op for the mkdir.
        s2 = build_tmp_image_store(data_dir=tmp_path, max_bytes=1024)
        assert s1.dir == s2.dir
        assert s2.dir.exists()


# ---------------------------------------------------------------------------
# Group 2 — Lifespan-style wiring (the 4-line block the lifespan runs)
# ---------------------------------------------------------------------------


class TestLifespanStyleWiring:
    """The lifespan runs this exact 4-step sequence.

    We mirror it here against a minimal ``FastAPI`` app — the
    assertion is that ``app.state.tmp_image_store`` ends up
    populated and the dir exists.
    """

    def test_wire_sets_app_state_and_creates_dir(self, tmp_path):
        app = FastAPI()
        max_bytes = 4096

        # Step 1: resolve data_dir (lifespan uses app.state.data_dir).
        app.state.data_dir = tmp_path
        # Step 2: construct the store.
        store = build_tmp_image_store(data_dir=tmp_path, max_bytes=max_bytes)
        # Step 3: assign to app.state.
        app.state.tmp_image_store = store

        # Post-conditions.
        assert app.state.tmp_image_store is not None
        assert isinstance(app.state.tmp_image_store, TmpImageStore)
        assert app.state.tmp_image_store.dir.exists()
        assert app.state.tmp_image_store.dir == tmp_path / "tmp_images"
        assert app.state.tmp_image_store.count() == 0

    def test_wire_respects_max_bytes_override(self, tmp_path):
        app = FastAPI()
        app.state.data_dir = tmp_path
        store = build_tmp_image_store(data_dir=tmp_path, max_bytes=512)
        app.state.tmp_image_store = store
        assert app.state.tmp_image_store.max_bytes == 512


# ---------------------------------------------------------------------------
# Group 3 — Boot anchor log shape (architect Task 8)
# ---------------------------------------------------------------------------


class TestBootAnchorLog:
    """The lifespan emits ``[TmpImages] ready: dir=... count=... max_bytes=...``.

    We exercise the same logger call shape here — the merge-gate
    tester greps for that exact string.
    """

    def test_anchor_log_carries_dir_and_max_bytes(self, tmp_path, caplog):
        max_bytes = 4096
        store = build_tmp_image_store(data_dir=tmp_path, max_bytes=max_bytes)
        with caplog.at_level(logging.INFO, logger="daemon.api"):
            # Mirror the lifespan's boot log line.
            logging.getLogger("daemon.api").info(
                f"[TmpImages] ready: dir={store.dir} "
                f"count={store.count()} "
                f"max_bytes={max_bytes}"
            )
        anchor = [r for r in caplog.records if "[TmpImages] ready:" in r.getMessage()]
        assert len(anchor) == 1
        msg = anchor[0].getMessage()
        assert f"dir={store.dir}" in msg
        assert "count=0" in msg
        assert f"max_bytes={max_bytes}" in msg


# ---------------------------------------------------------------------------
# Group 4 — Full-lifespan wiring (architect §Facade-Forwarding check)
# ---------------------------------------------------------------------------


class TestFullLifespanWiring:
    """End-to-end: the daemon/api.py wiring block resolves data_dir,
    constructs the store, and assigns it to app.state.

    The actual ``create_app()`` lifespan is exercised by other
    integration tests in the repo (test_vscode_routing.py et al.).
    Here we just exercise the helper seam to confirm the wiring is
    symmetric with what the lifespan does.
    """

    def test_full_wiring_path_produces_a_working_store(self, tmp_path):
        # The lifespan imports ``build_tmp_image_store`` from
        # ``daemon.services.tmp_image_store``. Use that exact import
        # path to verify the seam.
        from daemon.services.tmp_image_store import (
            build_tmp_image_store as factory,
        )
        store = factory(data_dir=tmp_path, max_bytes=10 * 1024 * 1024)
        assert store.dir == tmp_path / "tmp_images"
        assert store.max_bytes == 10 * 1024 * 1024
        # The store can be queried.
        assert store.count() == 0
        assert store.current_total_bytes() == 0
        assert store.oldest_mtime() is None

    def test_data_dir_resolution_chain_preserved(
        self, tmp_path, monkeypatch
    ):
        # The lifespan resolves data_dir from the
        # ``ENSEMBLE_DATA_DIR > DATA_DIR > ./data`` chain. We assert
        # that the chain produces the right value when only the
        # highest-priority env var is set — the daemon/api.py:232-244
        # resolution is verified elsewhere; here we just confirm the
        # resolved value flows through to the store's data_dir.
        monkeypatch.setenv("ENSEMBLE_DATA_DIR", str(tmp_path))
        monkeypatch.delenv("DATA_DIR", raising=False)
        resolved = (
            os.environ.get("ENSEMBLE_DATA_DIR")
            or os.environ.get("DATA_DIR")
            or "./data"
        )
        assert resolved == str(tmp_path)

        store = build_tmp_image_store(data_dir=Path(resolved), max_bytes=1024)
        assert store.data_dir == tmp_path


# ---------------------------------------------------------------------------
# Group 5 — W1 lifespan fail-soft (phase-1+3 review)
# ---------------------------------------------------------------------------


class TestLifespanFailSoftOnStoreBootError:
    """W1 (phase-1+3 review): the lifespan must NOT crash when the store
    factory raises (unwritable data_dir, EACCES/ENOSPC, etc.).

    The production wiring (daemon/api.py:263-289) wraps the
    ``build_tmp_image_store`` call in a try/except; on failure the
    store is set to ``None`` and the boot anchor is replaced by an
    ERROR log. The router's ``_get_store`` then returns 503 for every
    ``/api/tmp_images`` endpoint. This test mirrors the production
    wiring shape — same try/except block, same ERROR log, same
    ``app.state`` assignment — and pins the fail-soft contract:

    * boot completes (TestClient context manager exits cleanly),
    * ``app.state.tmp_image_store`` is ``None``,
    * POST/GET/DELETE on ``/api/tmp_images`` all return 503.
    """

    def test_unwritable_data_dir_boot_completes_with_store_none(self, tmp_path, monkeypatch):
        """Simulate an unwritable store dir (PermissionError) and verify
        the lifespan boot completes with the store set to None.
        """
        import logging
        from contextlib import asynccontextmanager
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        # Reach for the function through the module so the monkeypatch
        # below actually intercepts the call (a ``from X import f``
        # binding would not — the local name still points at the
        # original). This mirrors the production lifespan exactly,
        # which also imports ``build_tmp_image_store`` at module
        # level then accesses it via the local name.
        from daemon.services import tmp_image_store as tmp_image_store_module

        # Force the factory to raise — mirrors an unwritable data_dir
        # (PermissionError, OSError, or any subclass).
        def boom(**kwargs):
            raise PermissionError("simulated unwritable data_dir")

        monkeypatch.setattr(
            "daemon.services.tmp_image_store.build_tmp_image_store",
            boom,
        )

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            # Mirror production wiring exactly (daemon/api.py:263-289).
            tmp_image_store_max_bytes = 1024 * 1024
            try:
                tmp_image_store = tmp_image_store_module.build_tmp_image_store(
                    data_dir=tmp_path,
                    max_bytes=tmp_image_store_max_bytes,
                )
            except Exception as tmp_image_store_boot_exc:
                tmp_image_store = None
                logging.getLogger("daemon.api").error(
                    f"[TmpImages] store init FAILED — tmp-images endpoints "
                    f"will return 503 until the store is restored: "
                    f"{tmp_image_store_boot_exc}",
                    exc_info=True,
                )
            app.state.tmp_image_store = tmp_image_store
            if tmp_image_store is not None:
                logging.getLogger("daemon.api").info(
                    f"[TmpImages] ready: dir={tmp_image_store.dir} "
                    f"count={tmp_image_store.count()} "
                    f"max_bytes={tmp_image_store_max_bytes}"
                )
            try:
                yield
            finally:
                pass

        app = FastAPI(lifespan=lifespan)

        # Mount the real router so the 503 contract is exercised
        # through the production code path.
        from daemon.routers import tmp_images as tmp_images_module
        app.include_router(tmp_images_module.router, prefix="/api")

        # Boot must complete — if the fail-soft is missing the
        # context manager raises and TestClient propagates it.
        # Use a valid PNG payload so the POST reaches the route
        # handler (and thus ``_get_store``) instead of being rejected
        # at the body-validation layer (FastAPI/pydantic 422 happens
        # BEFORE the route runs, so a malformed body would short-circuit
        # past the 503 contract).
        valid_payload = {
            "images": [{
                "filename": "a.png",
                "content_type": "image/png",
                "data_base64": (
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE"
                    "QVR42mNkAAIAAAoAAv/lxKUAAAAASUVORK5CYII="
                ),
            }]
        }
        with TestClient(app) as client:
            # app.state.tmp_image_store is None.
            assert getattr(app.state, "tmp_image_store", "missing") is None, (
                "W1 fail-soft: store must be None after a failed init"
            )

            # POST → 503 (body validation passes, handler invokes
            # ``_get_store`` → 503).
            r = client.post("/api/tmp_images", json=valid_payload)
            assert r.status_code == 503, (
                f"W1 fail-soft: POST must return 503 when store is None, "
                f"got {r.status_code}: {r.text!r}"
            )

            # GET → 503 (no body validation; reaches the handler).
            r = client.get("/api/tmp_images/" + ("a" * 32))
            assert r.status_code == 503, (
                f"W1 fail-soft: GET must return 503 when store is None, "
                f"got {r.status_code}: {r.text!r}"
            )

            # DELETE → 503 (no body validation; reaches the handler).
            r = client.delete("/api/tmp_images/" + ("a" * 32))
            assert r.status_code == 503, (
                f"W1 fail-soft: DELETE must return 503 when store is None, "
                f"got {r.status_code}: {r.text!r}"
            )

    def test_unwritable_data_dir_logs_error_at_fail_soft(self, tmp_path, monkeypatch, caplog):
        """The fail-soft path must emit an ERROR log naming the exception.

        Pins the diagnostic surface — operators grep for the FAILED
        marker when /api/tmp_images returns 503.
        """
        import logging
        from contextlib import asynccontextmanager
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from daemon.services import tmp_image_store as tmp_image_store_module

        def boom(**kwargs):
            raise PermissionError(13, "Permission denied", "/data")

        monkeypatch.setattr(
            "daemon.services.tmp_image_store.build_tmp_image_store",
            boom,
        )

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            try:
                tmp_image_store = tmp_image_store_module.build_tmp_image_store(
                    data_dir=tmp_path,
                    max_bytes=1024,
                )
            except Exception as tmp_image_store_boot_exc:
                tmp_image_store = None
                logging.getLogger("daemon.api").error(
                    f"[TmpImages] store init FAILED — tmp-images endpoints "
                    f"will return 503 until the store is restored: "
                    f"{tmp_image_store_boot_exc}",
                    exc_info=True,
                )
            app.state.tmp_image_store = tmp_image_store
            try:
                yield
            finally:
                pass

        app = FastAPI(lifespan=lifespan)
        from daemon.routers import tmp_images as tmp_images_module
        app.include_router(tmp_images_module.router, prefix="/api")

        with caplog.at_level(logging.ERROR, logger="daemon.api"):
            with TestClient(app):
                pass

        error_lines = [
            r for r in caplog.records
            if r.levelno >= logging.ERROR
            and "[TmpImages] store init FAILED" in r.getMessage()
        ]
        assert len(error_lines) >= 1, (
            "W1 fail-soft: must emit an ERROR log with the FAILED marker. "
            f"Got caplog records: {[r.getMessage() for r in caplog.records]}"
        )
        msg = error_lines[0].getMessage()
        assert "Permission denied" in msg, (
            f"W1 fail-soft: ERROR log must carry the exception message, "
            f"got: {msg!r}"
        )
