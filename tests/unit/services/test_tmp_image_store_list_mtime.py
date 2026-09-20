"""Unit tests for ``TmpImageStore.list_ids_with_mtime`` (phase 3, Task 1).

Pins the surface the retention sweep consumes:

* empty dir → ``[]``; missing dir → ``[]`` (caller may invoke before
  ``init()`` in tests).
* blobs AND sidecars are listed (the sweep needs the orphan-sidecar
  view); mtimes match ``os.path.getmtime``.
* the hidden ``.gitignore`` (phase 1 Task 10) is EXCLUDED — it is a
  repo-hygiene artifact, never a stored image, and must never be
  reaped.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from daemon.services.tmp_image_store import TmpImageStore


_ID_A = "a" * 32
_ID_B = "b" * 32


def _make_store(tmp_path: Path) -> TmpImageStore:
    store = TmpImageStore(tmp_path, max_bytes=1024 * 1024)
    store.init()
    return store


class TestListIdsWithMtimeEmpty:
    def test_empty_dir_returns_empty_list(self, tmp_path):
        store = _make_store(tmp_path)
        assert store.list_ids_with_mtime() == []

    def test_missing_dir_returns_empty_list(self, tmp_path):
        store = TmpImageStore(tmp_path / "nope", max_bytes=1024 * 1024)
        assert store.list_ids_with_mtime() == []


class TestListIdsWithMtimeEntries:
    def test_blobs_and_sidecars_listed_with_mtimes(self, tmp_path):
        store = _make_store(tmp_path)
        store.save(_ID_A, b"one", "image/png")
        store.save(_ID_B, b"two", "image/gif")

        entries = dict(store.list_ids_with_mtime())

        # Blobs (extensionless) AND their .json sidecars are listed —
        # the sweep distinguishes them by suffix and needs both (the
        # orphan-sidecar view).
        assert set(entries) == {_ID_A, _ID_B, f"{_ID_A}.json", f"{_ID_B}.json"}
        for name, mtime in entries.items():
            assert mtime == os.path.getmtime(store.dir / name), (
                f"mtime for {name} must match os.path.getmtime"
            )

    def test_mixed_mtimes_preserved_per_file(self, tmp_path):
        store = _make_store(tmp_path)
        store.save(_ID_A, b"old", "image/png")
        store.save(_ID_B, b"new", "image/png")

        old_ts = time.time() - 40 * 86400
        os.utime(store.dir / _ID_A, (old_ts, old_ts))

        entries = dict(store.list_ids_with_mtime())
        assert entries[_ID_A] == old_ts
        assert entries[_ID_B] > old_ts

    def test_gitignore_excluded(self, tmp_path):
        store = _make_store(tmp_path)
        store.save(_ID_A, b"x", "image/png")
        gitignore = store.dir / ".gitignore"
        gitignore.write_text("", encoding="utf-8")
        # Age it to "older than any retention" — it must STILL be
        # excluded (age is irrelevant; the name is filtered).
        os.utime(gitignore, (0, 0))

        names = [name for name, _ in store.list_ids_with_mtime()]
        assert ".gitignore" not in names
        assert set(names) == {_ID_A, f"{_ID_A}.json"}

    def test_subdirectories_not_listed(self, tmp_path):
        store = _make_store(tmp_path)
        store.save(_ID_A, b"x", "image/png")
        (store.dir / "subdir").mkdir()

        names = [name for name, _ in store.list_ids_with_mtime()]
        assert "subdir" not in names
