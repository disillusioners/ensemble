"""Logging-behavior tests for ``TmpImageCleanupService`` (phase 3).

Pins the two log surfaces the plan + merge-gate tester rely on:

* WARNING + ``last_sweep_error`` on a forced sweep exception
  (whole-body defensive catch — mirrors
  ``JobLockSweepService.sweep_once``);
* the INFO reap line ``[TmpImages] reaped {n} image(s) older than
  {d}d: {ids}`` with a BOUNDED sample of ≤5 ids — a mass-reap tick
  (first sweep after a long downtime) must not flood the line;
* no reap INFO line when nothing was reaped (log-noise discipline).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

import daemon.services.tmp_image_cleanup_service as cleanup_module
from daemon.services.tmp_image_cleanup_service import TmpImageCleanupService
from daemon.services.tmp_image_store import TmpImageStore

_FROZEN_NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)
_FROZEN_CUTOFF = _FROZEN_NOW.timestamp() - 30 * 86400


def _make_store(tmp_path: Path) -> TmpImageStore:
    store = TmpImageStore(tmp_path, max_bytes=1024 * 1024)
    store.init()
    return store


def _seed_old(store: TmpImageStore, image_id: str) -> None:
    store.save(image_id, b"x", "image/png")
    sidecar = store.dir / f"{image_id}.json"
    meta = {
        "content_type": "image/png",
        "size_bytes": 1,
        "uploaded_at": datetime.fromtimestamp(
            _FROZEN_CUTOFF - 86400, tz=timezone.utc
        ).isoformat(),
        "sha256_hex": "0" * 64,
    }
    sidecar.write_text(json.dumps(meta), encoding="utf-8")
    os.utime(store.dir / image_id, (_FROZEN_CUTOFF - 86400,) * 2)
    os.utime(sidecar, (_FROZEN_CUTOFF - 86400,) * 2)


class TestForcedSweepExceptionLogging:
    async def test_warning_logged_with_exc_info_and_error_state(
        self, tmp_path, caplog, monkeypatch
    ):
        store = _make_store(tmp_path)
        svc = TmpImageCleanupService(store)

        def boom():
            raise RuntimeError("walk exploded")

        monkeypatch.setattr(store, "list_ids_with_mtime", boom)
        with caplog.at_level(logging.WARNING, logger=cleanup_module.__name__):
            deleted = await svc.sweep_once()

        assert deleted == 0
        warnings = [
            r
            for r in caplog.records
            if "cleanup sweep failed" in r.getMessage()
        ]
        assert len(warnings) == 1
        record = warnings[0]
        assert record.levelno == logging.WARNING
        assert "RuntimeError" in record.getMessage()
        assert "next tick will retry" in record.getMessage()
        assert record.exc_info is not None, (
            "the defensive catch logs with exc_info=True (traceback "
            "in the log for forensics)"
        )
        assert svc.last_sweep_error == "RuntimeError: walk exploded"
        assert svc.last_sweep_deleted == 0
        assert svc.last_sweep_at is not None


class TestReapLogLine:
    async def test_reap_line_has_count_retention_and_bounded_sample(
        self, tmp_path, caplog, monkeypatch
    ):
        store = _make_store(tmp_path)
        ids = [format(i, "032x") for i in range(1, 8)]  # 7 old entries
        for image_id in ids:
            _seed_old(store, image_id)

        svc = TmpImageCleanupService(store, retention_days=30)
        monkeypatch.setattr(cleanup_module, "now_utc", lambda: _FROZEN_NOW)
        with caplog.at_level(logging.INFO, logger=cleanup_module.__name__):
            deleted = await svc.sweep_once()

        assert deleted == 7
        infos = [
            r
            for r in caplog.records
            if "[TmpImages] reaped" in r.getMessage()
        ]
        assert len(infos) == 1, "exactly one reap line per sweep tick"
        msg = infos[0].getMessage()
        assert infos[0].levelno == logging.INFO
        assert "[TmpImages] reaped 7 image(s) older than 30d:" in msg
        # Bounded sample: exactly ≤5 ids, drawn from the reaped set.
        # scandir order is unspecified (the plan: do not over-specify)
        # — pin the CAP and the SUBSET property, not which ids made
        # the sample.
        sample = msg.split(":", 1)[1]
        listed = [tok.strip() for tok in sample.split(",") if tok.strip()]
        assert len(listed) == 5, f"sample must cap at 5 ids, got: {sample}"
        assert set(listed) <= set(ids)
        assert len(set(ids) - set(listed)) == 2, (
            "exactly 2 of the 7 reaped ids must be omitted from the "
            "bounded sample"
        )

    async def test_no_reap_line_when_nothing_reaped(
        self, tmp_path, caplog, monkeypatch
    ):
        store = _make_store(tmp_path)
        svc = TmpImageCleanupService(store, retention_days=30)
        monkeypatch.setattr(cleanup_module, "now_utc", lambda: _FROZEN_NOW)
        with caplog.at_level(logging.INFO, logger=cleanup_module.__name__):
            deleted = await svc.sweep_once()
        assert deleted == 0
        assert not [
            r
            for r in caplog.records
            if "[TmpImages] reaped" in r.getMessage()
        ], "a no-op tick must not emit a reap line (log-noise discipline)"

    async def test_start_log_line_shape(self, tmp_path, caplog):
        store = _make_store(tmp_path)
        svc = TmpImageCleanupService(
            store, interval_seconds=3600, retention_days=30
        )
        with caplog.at_level(logging.INFO, logger=cleanup_module.__name__):
            svc.start()
        try:
            started = [
                r
                for r in caplog.records
                if "TmpImageCleanupService started" in r.getMessage()
            ]
            assert len(started) == 1
            msg = started[0].getMessage()
            assert "interval=3600s" in msg
            assert "retention=30d" in msg
        finally:
            await svc.stop()
