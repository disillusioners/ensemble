"""Unit tests for ``TmpImageCleanupService`` (phase 3, Task 3 + 6).

Pins the sweep semantics the plan + architect rulings demand:

* start/stop round-trip (real asyncio task, immediate first tick);
* sweep deletes strictly-old entries, retains fresh ones;
* age BOUNDARY — exactly-at-cutoff retained, strictly-older deleted;
* age precedence — sidecar ``uploaded_at`` beats mtime (both
  directions); unparseable/missing sidecar falls back to mtime;
* orphan sidecar / orphan blob reaped by their OWN mtime;
* interval clamp (negative → 1, mirrors ``JobLockSweepService``);
* missing store dir → 0 + WARNING (not a crash);
* locked/unreadable file skipped without crashing the tick;
* ``enabled=False`` INTERNAL TEST SEAM — ``start()`` spawns nothing,
  no DISABLED log line (that branch is deleted per amendment #15);
* FE DELETE ∥ sweep race — idempotent unlink, count excludes the
  lost race.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from daemon.services.tmp_image_cleanup_service import TmpImageCleanupService
from daemon.services.tmp_image_store import TmpImageStore

_FROZEN_NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)
_DAYS = 30
_FROZEN_CUTOFF = _FROZEN_NOW.timestamp() - _DAYS * 86400


def _make_store(tmp_path: Path) -> TmpImageStore:
    store = TmpImageStore(tmp_path, max_bytes=1024 * 1024)
    store.init()
    return store


def _backdate_mtime(path: Path, ts: float) -> None:
    os.utime(path, (ts, ts))


def _set_sidecar_uploaded(store: TmpImageStore, image_id: str, dt: datetime) -> None:
    sidecar = store.dir / f"{image_id}.json"
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    meta["uploaded_at"] = dt.isoformat()
    sidecar.write_text(json.dumps(meta), encoding="utf-8")


def _old_dt(seconds_before_cutoff: float = 86400) -> datetime:
    """A datetime strictly older than the frozen cutoff by default."""
    return datetime.fromtimestamp(
        _FROZEN_CUTOFF - seconds_before_cutoff, tz=timezone.utc
    )


# ---------------------------------------------------------------------------
# Group 1 — lifecycle (start/stop round-trip + enabled test seam)
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_start_stop_round_trip(self, tmp_path):
        store = _make_store(tmp_path)
        svc = TmpImageCleanupService(store, interval_seconds=3600)
        svc.start()
        assert svc._task is not None
        assert not svc._task.done()
        # Idempotent start — second call while alive is a no-op.
        first_task = svc._task
        svc.start()
        assert svc._task is first_task
        await svc.stop()
        assert svc._task is None
        assert first_task.done()

    async def test_stop_without_start_is_silent_noop(self, tmp_path):
        store = _make_store(tmp_path)
        svc = TmpImageCleanupService(store)
        await svc.stop()  # must not raise
        assert svc._task is None

    async def test_start_runs_first_tick_immediately(
        self, tmp_path, monkeypatch
    ):
        # FIRST TICK IMMEDIATE: _run sweeps before the first sleep.
        store = _make_store(tmp_path)
        old_id = "a" * 32
        store.save(old_id, b"old", "image/png")
        _set_sidecar_uploaded(store, old_id, _old_dt())
        _backdate_mtime(store.dir / old_id, _FROZEN_CUTOFF - 86400)
        _backdate_mtime(store.dir / f"{old_id}.json", _FROZEN_CUTOFF - 86400)

        svc = TmpImageCleanupService(
            store, interval_seconds=3600
        )
        freeze_now(monkeypatch)
        svc.start()
        try:
            for _ in range(100):
                if svc.last_sweep_at is not None:
                    break
                await asyncio.sleep(0.01)
            assert svc.last_sweep_at is not None, (
                "first tick must run before the first sleep"
            )
            assert not (store.dir / old_id).exists()
        finally:
            await svc.stop()

    async def test_enabled_false_spawns_no_task_and_no_disabled_log(
        self, tmp_path, caplog, monkeypatch
    ):
        # INTERNAL TEST SEAM ONLY (architect amendment #15). No
        # production path passes False; the DISABLED log branch is
        # DELETED — its absence is pinned here.
        store = _make_store(tmp_path)
        svc = TmpImageCleanupService(store, enabled=False)
        with caplog.at_level(logging.DEBUG):
            svc.start()
        assert svc._task is None
        assert not any("DISABLED" in r.getMessage() for r in caplog.records), (
            "the DISABLED log branch was deleted — it must not reappear"
        )
        # The seam still allows a direct deterministic sweep.
        assert await svc.sweep_once() == 0


def freeze_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze ``now_utc`` for deterministic cutoff arithmetic.

    Uses pytest's ``monkeypatch`` fixture so the patch is auto-
    restored — a leaked module-global freeze would silently re-anchor
    every other test's cutoff.
    """
    import daemon.services.tmp_image_cleanup_service as mod

    monkeypatch.setattr(mod, "now_utc", lambda: _FROZEN_NOW)


# ---------------------------------------------------------------------------
# Group 2 — sweep semantics
# ---------------------------------------------------------------------------


class TestSweepDeletesOldRetainsNew:
    async def test_old_pair_deleted_new_pair_retained(
        self, tmp_path, monkeypatch
    ):
        store = _make_store(tmp_path)
        old_id, new_id = "a" * 32, "f" * 32
        for image_id in (old_id, new_id):
            store.save(image_id, b"payload", "image/png")
            _set_sidecar_uploaded(store, image_id, _old_dt())
            _backdate_mtime(store.dir / image_id, _FROZEN_CUTOFF - 86400)
            _backdate_mtime(
                store.dir / f"{image_id}.json", _FROZEN_CUTOFF - 86400
            )
        # The NEW pair keeps fresh sidecar uploaded_at (default from
        # save) — and fresh mtimes: only the old pair is backdated.
        _set_sidecar_uploaded(store, new_id, _FROZEN_NOW)
        os.utime(store.dir / new_id, (time.time(), time.time()))
        os.utime(store.dir / f"{new_id}.json", (time.time(), time.time()))

        svc = TmpImageCleanupService(store, retention_days=_DAYS)
        freeze_now(monkeypatch)
        deleted = await svc.sweep_once()

        assert deleted == 1
        assert not (store.dir / old_id).exists()
        assert not (store.dir / f"{old_id}.json").exists()
        assert (store.dir / new_id).exists()
        assert (store.dir / f"{new_id}.json").exists()
        assert svc.last_sweep_deleted == 1
        assert svc.last_sweep_error is None
        assert svc.last_sweep_at is not None

    async def test_sidecar_uploaded_at_beats_old_mtime_retains(
        self, tmp_path, monkeypatch
    ):
        # mtime says ancient, sidecar says fresh → RETAINED (sidecar
        # precedence; the ⚠ dispatcher refinement).
        store = _make_store(tmp_path)
        image_id = "e" * 32
        store.save(image_id, b"x", "image/png")
        _set_sidecar_uploaded(store, image_id, _FROZEN_NOW)
        _backdate_mtime(store.dir / image_id, _FROZEN_CUTOFF - 86400)
        _backdate_mtime(store.dir / f"{image_id}.json", _FROZEN_CUTOFF - 86400)

        svc = TmpImageCleanupService(store, retention_days=_DAYS)
        freeze_now(monkeypatch)
        assert await svc.sweep_once() == 0
        assert (store.dir / image_id).exists()

    async def test_sidecar_uploaded_at_beats_fresh_mtime_reaps(
        self, tmp_path, monkeypatch
    ):
        # mtime says fresh (restore tool / cp -p mishap), sidecar says
        # ancient → REAPED (sidecar precedence, other direction).
        store = _make_store(tmp_path)
        image_id = "g" * 32
        store.save(image_id, b"x", "image/png")
        _set_sidecar_uploaded(store, image_id, _old_dt())
        os.utime(store.dir / image_id, (time.time(), time.time()))
        os.utime(store.dir / f"{image_id}.json", (time.time(), time.time()))

        svc = TmpImageCleanupService(store, retention_days=_DAYS)
        freeze_now(monkeypatch)
        assert await svc.sweep_once() == 1
        assert not (store.dir / image_id).exists()

    async def test_unparseable_sidecar_falls_back_to_mtime(
        self, tmp_path, monkeypatch
    ):
        store = _make_store(tmp_path)
        image_id = "c" * 32
        store.save(image_id, b"x", "image/png")
        (store.dir / f"{image_id}.json").write_text(
            "{not json", encoding="utf-8"
        )
        _backdate_mtime(store.dir / image_id, _FROZEN_CUTOFF - 86400)
        _backdate_mtime(store.dir / f"{image_id}.json", _FROZEN_CUTOFF - 86400)

        svc = TmpImageCleanupService(store, retention_days=_DAYS)
        freeze_now(monkeypatch)
        assert await svc.sweep_once() == 1
        assert not (store.dir / image_id).exists()

    async def test_missing_sidecar_falls_back_to_blob_mtime(
        self, tmp_path, monkeypatch
    ):
        # Orphan BLOB (sidecar never landed) — aged by its own mtime.
        store = _make_store(tmp_path)
        image_id = "h" * 32
        store.save(image_id, b"x", "image/png")
        (store.dir / f"{image_id}.json").unlink()
        _backdate_mtime(store.dir / image_id, _FROZEN_CUTOFF - 86400)

        svc = TmpImageCleanupService(store, retention_days=_DAYS)
        freeze_now(monkeypatch)
        assert await svc.sweep_once() == 1
        assert not (store.dir / image_id).exists()

    async def test_orphan_sidecar_aged_by_own_mtime_reaped(
        self, tmp_path, monkeypatch
    ):
        store = _make_store(tmp_path)
        image_id = "b" * 32
        store.save(image_id, b"gone", "image/png")
        (store.dir / image_id).unlink()  # blob already removed
        _backdate_mtime(
            store.dir / f"{image_id}.json", _FROZEN_CUTOFF - 86400
        )

        svc = TmpImageCleanupService(store, retention_days=_DAYS)
        freeze_now(monkeypatch)
        assert await svc.sweep_once() == 1
        assert not (store.dir / f"{image_id}.json").exists()

    async def test_young_orphan_sidecar_retained(
        self, tmp_path, monkeypatch
    ):
        store = _make_store(tmp_path)
        image_id = "y" * 32
        store.save(image_id, b"gone", "image/png")
        (store.dir / image_id).unlink()
        # Sidecar stays fresh (save default) → retained.

        svc = TmpImageCleanupService(store, retention_days=_DAYS)
        freeze_now(monkeypatch)
        assert await svc.sweep_once() == 0
        assert (store.dir / f"{image_id}.json").exists()

    async def test_gitignore_never_reaped(self, tmp_path, monkeypatch):
        store = _make_store(tmp_path)
        (store.dir / ".gitignore").write_text("", encoding="utf-8")
        os.utime(store.dir / ".gitignore", (0, 0))

        svc = TmpImageCleanupService(store, retention_days=_DAYS)
        freeze_now(monkeypatch)
        assert await svc.sweep_once() == 0
        assert (store.dir / ".gitignore").exists()


class TestAgeBoundary:
    async def test_exactly_at_cutoff_retained(
        self, tmp_path, monkeypatch
    ):
        store = _make_store(tmp_path)
        image_id = "d" * 32
        store.save(image_id, b"exact", "image/png")
        _set_sidecar_uploaded(
            store, image_id, datetime.fromtimestamp(_FROZEN_CUTOFF, tz=timezone.utc)
        )
        _backdate_mtime(store.dir / image_id, _FROZEN_CUTOFF)
        _backdate_mtime(store.dir / f"{image_id}.json", _FROZEN_CUTOFF)

        svc = TmpImageCleanupService(store, retention_days=_DAYS)
        freeze_now(monkeypatch)
        assert await svc.sweep_once() == 0, (
            "exactly-at-cutoff must be RETAINED (only strictly-older "
            "entries are reaped)"
        )
        assert (store.dir / image_id).exists()

    async def test_one_second_past_cutoff_deleted(
        self, tmp_path, monkeypatch
    ):
        store = _make_store(tmp_path)
        image_id = "s" * 32
        store.save(image_id, b"just past", "image/png")
        _set_sidecar_uploaded(
            store,
            image_id,
            datetime.fromtimestamp(_FROZEN_CUTOFF - 1, tz=timezone.utc),
        )
        _backdate_mtime(store.dir / image_id, _FROZEN_CUTOFF - 1)
        _backdate_mtime(store.dir / f"{image_id}.json", _FROZEN_CUTOFF - 1)

        svc = TmpImageCleanupService(store, retention_days=_DAYS)
        freeze_now(monkeypatch)
        assert await svc.sweep_once() == 1
        assert not (store.dir / image_id).exists()


# ---------------------------------------------------------------------------
# Group 3 — constructor + defensive paths
# ---------------------------------------------------------------------------


class TestConstructor:
    def test_negative_interval_clamps_to_one(self, tmp_path):
        store = _make_store(tmp_path)
        svc = TmpImageCleanupService(store, interval_seconds=-5)
        assert svc.interval_seconds == 1

    def test_zero_interval_clamps_to_one(self, tmp_path):
        store = _make_store(tmp_path)
        svc = TmpImageCleanupService(store, interval_seconds=0)
        assert svc.interval_seconds == 1

    def test_retention_days_floor(self, tmp_path):
        store = _make_store(tmp_path)
        svc = TmpImageCleanupService(store, retention_days=0)
        assert svc.retention_days == 1


class TestDefensivePaths:
    async def test_missing_dir_returns_zero_with_warning(
        self, tmp_path, caplog
    ):
        store = TmpImageStore(tmp_path / "nope", max_bytes=1024 * 1024)
        svc = TmpImageCleanupService(store)
        with caplog.at_level(logging.WARNING):
            deleted = await svc.sweep_once()
        assert deleted == 0
        assert svc.last_sweep_error == "store directory missing"
        warnings = [
            r
            for r in caplog.records
            if "store dir missing" in r.getMessage()
        ]
        assert len(warnings) == 1

    async def test_locked_file_skipped_without_crash(
        self, tmp_path, monkeypatch
    ):
        # A per-file PermissionError (EBUSY/EACCES/read-only class)
        # must skip THAT file, still reap the others, and exclude the
        # locked one from the count.
        store = _make_store(tmp_path)
        locked_id, reapable_id = "1" * 32, "2" * 32
        for image_id in (locked_id, reapable_id):
            store.save(image_id, b"x", "image/png")
            _set_sidecar_uploaded(store, image_id, _old_dt())
            _backdate_mtime(store.dir / image_id, _FROZEN_CUTOFF - 86400)
            _backdate_mtime(
                store.dir / f"{image_id}.json", _FROZEN_CUTOFF - 86400
            )

        svc = TmpImageCleanupService(store, retention_days=_DAYS)
        freeze_now(monkeypatch)

        original_delete = store.delete

        def delete_except_locked(image_id: str) -> bool:
            if image_id == locked_id:
                raise PermissionError(
                    f"[Errno 13] Permission denied: {image_id}"
                )
            return original_delete(image_id)

        store.delete = delete_except_locked  # type: ignore[method-assign]
        deleted = await svc.sweep_once()

        assert deleted == 1, "locked file must be excluded from the count"
        assert (store.dir / locked_id).exists(), "locked file must survive"
        assert not (store.dir / reapable_id).exists(), (
            "the other old file must still be reaped"
        )

    async def test_chmod_000_sidecar_degrades_to_mtime_fallback(
        self, tmp_path, monkeypatch
    ):
        if os.geteuid() == 0:  # pragma: no cover — root ignores modes
            pytest.skip("root ignores file modes")
        # POSIX note: chmod-000 does NOT block unlink (unlink needs
        # write on the DIRECTORY, not the file) — the deterministic
        # unlink-OSError skip is pinned by
        # ``test_locked_file_skipped_without_crash`` (monkeypatch).
        # What chmod-000 DOES pin for real: the sidecar READ fails
        # with EACCES → the sweep degrades to the mtime fallback and
        # still reaps the pair (no crash, no exception leak).
        store = _make_store(tmp_path)
        image_id = "3" * 32
        store.save(image_id, b"x", "image/png")
        _backdate_mtime(store.dir / image_id, _FROZEN_CUTOFF - 86400)
        _backdate_mtime(store.dir / f"{image_id}.json", _FROZEN_CUTOFF - 86400)
        (store.dir / f"{image_id}.json").chmod(0o000)

        svc = TmpImageCleanupService(store, retention_days=_DAYS)
        freeze_now(monkeypatch)
        deleted = await svc.sweep_once()
        assert deleted == 1, (
            "unreadable sidecar must degrade to the mtime fallback, "
            "not block the reap"
        )
        assert not (store.dir / image_id).exists()

    async def test_delete_race_lost_file_not_counted(
        self, tmp_path, monkeypatch
    ):
        # FE DELETE ∥ sweep race: the file vanishes between the
        # listing and the unlink. FileNotFoundError is swallowed
        # silently (architect §7) and the lost race is NOT counted.
        store = _make_store(tmp_path)
        raced_id = "5" * 32
        store.save(raced_id, b"x", "image/png")
        _set_sidecar_uploaded(store, raced_id, _old_dt())
        _backdate_mtime(store.dir / raced_id, _FROZEN_CUTOFF - 86400)
        _backdate_mtime(store.dir / f"{raced_id}.json", _FROZEN_CUTOFF - 86400)

        def raced_delete(image_id: str) -> bool:
            # Simulate the FE DELETE winning the race: both files are
            # already gone by the time the sweep's unlink lands.
            raise FileNotFoundError(f"gone: {image_id}")

        store.delete = raced_delete  # type: ignore[method-assign]
        svc = TmpImageCleanupService(store, retention_days=_DAYS)
        freeze_now(monkeypatch)
        deleted = await svc.sweep_once()
        assert deleted == 0, "a fully-raced delete reaped nothing"
        assert svc.last_sweep_error is None, (
            "the race is expected traffic — not an error"
        )

    async def test_whole_body_exception_returns_zero_and_sets_error(
        self, tmp_path, caplog
    ):
        store = _make_store(tmp_path)
        svc = TmpImageCleanupService(store)
        # Force an exception OUTSIDE the per-file handlers (whole-body
        # defensive catch; detailed log pins live in the logging test).
        store.list_ids_with_mtime = (  # type: ignore[method-assign]
            lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        with caplog.at_level(logging.WARNING):
            deleted = await svc.sweep_once()
        assert deleted == 0
        assert svc.last_sweep_error is not None
        assert "RuntimeError" in svc.last_sweep_error
        assert any(
            "cleanup sweep failed" in r.getMessage() for r in caplog.records
        )
