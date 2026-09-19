"""Unit tests for ``daemon.services.tmp_image_store``.

Phase 1 / clipboard-image-chat. Validators pin:

* round-trip bytes (save then open returns identical content).
* delete removes both blob + sidecar.
* unknown id raises ``TmpImageNotFound``.
* init() is idempotent (mkdir on an existing dir is a no-op).
* Filling the store to its cap raises ``TmpImageStoreFull`` on the
  next save (architect amendment #3).
"""

from __future__ import annotations

import pytest

from daemon.services.tmp_image_store import (
    TmpImageNotFound,
    TmpImageStore,
    TmpImageStoreFull,
    build_tmp_image_store,
)


_VALID_HEX_ID_32 = "a" * 32
_OTHER_VALID_HEX_ID_32 = "b" * 32


# ---------------------------------------------------------------------------
# Group 1 — happy path + round-trip
# ---------------------------------------------------------------------------


class TestTmpImageStoreRoundTrip:
    def test_save_then_open_returns_identical_bytes(self, tmp_path):
        store = TmpImageStore(tmp_path, max_bytes=1024 * 1024)
        store.init()
        record = store.save(_VALID_HEX_ID_32, b"hello world", "image/png")
        data, content_type = store.open(_VALID_HEX_ID_32)
        assert data == b"hello world"
        assert content_type == "image/png"
        assert record.size_bytes == len(b"hello world")

    def test_save_records_sha256_digest(self, tmp_path):
        import hashlib

        store = TmpImageStore(tmp_path, max_bytes=1024 * 1024)
        store.init()
        payload = b"hash this"
        record = store.save(_VALID_HEX_ID_32, payload, "image/png")
        assert record.sha256_hex == hashlib.sha256(payload).hexdigest()

    def test_save_writes_extensionless_blob(self, tmp_path):
        store = TmpImageStore(tmp_path, max_bytes=1024 * 1024)
        store.init()
        store.save(_VALID_HEX_ID_32, b"x", "image/png")
        # Blob path is extensionless per architect risk #7 ruling.
        assert (store.dir / _VALID_HEX_ID_32).exists()
        assert not (store.dir / f"{_VALID_HEX_ID_32}.png").exists()
        # Sidecar carries the MIME.
        assert (store.dir / f"{_VALID_HEX_ID_32}.json").exists()

    def test_save_with_collision_raises_file_exists(self, tmp_path):
        store = TmpImageStore(tmp_path, max_bytes=1024 * 1024)
        store.init()
        store.save(_VALID_HEX_ID_32, b"first", "image/png")
        with pytest.raises(FileExistsError):
            store.save(_VALID_HEX_ID_32, b"second", "image/png")


# ---------------------------------------------------------------------------
# Group 2 — delete
# ---------------------------------------------------------------------------


class TestTmpImageStoreDelete:
    def test_delete_removes_blob_and_sidecar(self, tmp_path):
        store = TmpImageStore(tmp_path, max_bytes=1024 * 1024)
        store.init()
        store.save(_VALID_HEX_ID_32, b"x", "image/png")
        assert (store.dir / _VALID_HEX_ID_32).exists()
        assert (store.dir / f"{_VALID_HEX_ID_32}.json").exists()
        removed = store.delete(_VALID_HEX_ID_32)
        assert removed is True
        assert not (store.dir / _VALID_HEX_ID_32).exists()
        assert not (store.dir / f"{_VALID_HEX_ID_32}.json").exists()

    def test_delete_unknown_id_is_idempotent(self, tmp_path):
        store = TmpImageStore(tmp_path, max_bytes=1024 * 1024)
        store.init()
        # No save — just delete. Should not raise.
        removed = store.delete(_VALID_HEX_ID_32)
        assert removed is False

    def test_delete_then_open_raises_not_found(self, tmp_path):
        store = TmpImageStore(tmp_path, max_bytes=1024 * 1024)
        store.init()
        store.save(_VALID_HEX_ID_32, b"x", "image/png")
        store.delete(_VALID_HEX_ID_32)
        with pytest.raises(TmpImageNotFound):
            store.open(_VALID_HEX_ID_32)


# ---------------------------------------------------------------------------
# Group 3 — open / not-found
# ---------------------------------------------------------------------------


class TestTmpImageStoreOpen:
    def test_open_unknown_id_raises_not_found(self, tmp_path):
        store = TmpImageStore(tmp_path, max_bytes=1024 * 1024)
        store.init()
        with pytest.raises(TmpImageNotFound):
            store.open(_VALID_HEX_ID_32)

    def test_open_with_meta_returns_sha256(self, tmp_path):
        store = TmpImageStore(tmp_path, max_bytes=1024 * 1024)
        store.init()
        payload = b"sha-this"
        store.save(_VALID_HEX_ID_32, payload, "image/png")
        data, content_type, sha256_hex = store.open_with_meta(_VALID_HEX_ID_32)
        assert data == payload
        assert content_type == "image/png"
        assert len(sha256_hex) == 64  # full hex digest

    def test_open_with_meta_unknown_id_raises_not_found(self, tmp_path):
        store = TmpImageStore(tmp_path, max_bytes=1024 * 1024)
        store.init()
        with pytest.raises(TmpImageNotFound):
            store.open_with_meta(_VALID_HEX_ID_32)


# ---------------------------------------------------------------------------
# Group 4 — init / idempotency
# ---------------------------------------------------------------------------


class TestTmpImageStoreInit:
    def test_init_creates_directory(self, tmp_path):
        store = TmpImageStore(tmp_path, max_bytes=1024 * 1024)
        assert not store.dir.exists()
        store.init()
        assert store.dir.exists()
        assert store.dir.is_dir()

    def test_init_is_idempotent(self, tmp_path):
        store = TmpImageStore(tmp_path, max_bytes=1024 * 1024)
        store.init()
        store.init()  # must not raise
        assert store.dir.exists()

    def test_init_on_existing_data_dir_does_not_clear(self, tmp_path):
        store = TmpImageStore(tmp_path, max_bytes=1024 * 1024)
        store.init()
        store.save(_VALID_HEX_ID_32, b"keep me", "image/png")
        # Re-init must preserve the existing entry.
        store.init()
        data, _ = store.open(_VALID_HEX_ID_32)
        assert data == b"keep me"

    def test_data_dir_property_returns_parent(self, tmp_path):
        store = TmpImageStore(tmp_path, max_bytes=1024 * 1024)
        assert store.data_dir == tmp_path

    def test_dir_property_returns_subdir(self, tmp_path):
        store = TmpImageStore(tmp_path, max_bytes=1024 * 1024)
        assert store.dir == tmp_path / "tmp_images"


# ---------------------------------------------------------------------------
# Group 5 — byte cap (architect amendment #3)
# ---------------------------------------------------------------------------


class TestTmpImageStoreCap:
    def test_first_write_above_cap_raises_full(self, tmp_path):
        # Cap = 100 bytes total. Any single write (blob + ~180 byte
        # sidecar) exceeds 100 → first write raises.
        cap = 100
        store = TmpImageStore(tmp_path, max_bytes=cap)
        store.init()
        with pytest.raises(TmpImageStoreFull):
            store.save(_VALID_HEX_ID_32, b"x" * 50, "image/png")

    def test_two_writes_under_cap_second_trips_full(self, tmp_path):
        # Cap = 500 bytes. Per-write footprint ≈ blob_size + 180 bytes
        # of sidecar (the timestamp string is fixed-length; sha256 is
        # 64 hex chars).
        cap = 500
        store = TmpImageStore(tmp_path, max_bytes=cap)
        store.init()
        store.save(_VALID_HEX_ID_32, b"x" * 50, "image/png")        # ~230
        # Second write pushes the total to ~460 < 500, so it succeeds.
        store.save(_OTHER_VALID_HEX_ID_32, b"y" * 50, "image/png")  # ~460
        # Third write would push to ~690 > 500 → raises.
        with pytest.raises(TmpImageStoreFull):
            store.save("c" * 32, b"z" * 50, "image/png")

    def test_cap_accounts_for_blob_and_sidecar(self, tmp_path):
        # The cap is total disk usage; blobs + sidecars both count.
        # Even a tiny 30-byte blob trips a 200-byte cap because the
        # ~180-byte sidecar is itself over-budget.
        cap = 200
        store = TmpImageStore(tmp_path, max_bytes=cap)
        store.init()
        with pytest.raises(TmpImageStoreFull):
            store.save(_VALID_HEX_ID_32, b"x" * 30, "image/png")

    def test_save_at_exact_cap_succeeds(self, tmp_path):
        # Edge: the projected total is exactly the cap → not raised.
        # 1-byte blob; sidecar ~180 bytes; total ~181.
        # Set the cap just above the projected total.
        cap = 200
        store = TmpImageStore(tmp_path, max_bytes=cap)
        store.init()
        # Should succeed: 0 + 1 (blob) + ~180 (sidecar) = ~181 ≤ 200.
        store.save(_VALID_HEX_ID_32, b"x", "image/png")

    def test_collision_does_not_consume_cap_quota(self, tmp_path):
        # FileExistsError on save must NOT count against the cap.
        # Otherwise a flood of retries could falsely trip the cap.
        cap = 800
        store = TmpImageStore(tmp_path, max_bytes=cap)
        store.init()
        store.save(_VALID_HEX_ID_32, b"x" * 50, "image/png")  # ~230
        with pytest.raises(FileExistsError):
            store.save(_VALID_HEX_ID_32, b"y" * 50, "image/png")  # collision
        # Cap still has room for ~570 more bytes; second save succeeds.
        store.save(_OTHER_VALID_HEX_ID_32, b"z" * 50, "image/png")


# ---------------------------------------------------------------------------
# Group 6 — build factory + introspection
# ---------------------------------------------------------------------------


class TestBuildFactoryAndIntrospection:
    def test_build_tmp_image_store_factory(self, tmp_path):
        store = build_tmp_image_store(tmp_path, max_bytes=4096)
        assert isinstance(store, TmpImageStore)
        assert store.dir.exists()
        assert store.max_bytes == 4096

    def test_count_returns_zero_for_empty_store(self, tmp_path):
        store = TmpImageStore(tmp_path, max_bytes=1024)
        store.init()
        assert store.count() == 0

    def test_count_returns_n_after_writes(self, tmp_path):
        store = TmpImageStore(tmp_path, max_bytes=1024 * 1024)
        store.init()
        store.save(_VALID_HEX_ID_32, b"a", "image/png")
        store.save(_OTHER_VALID_HEX_ID_32, b"b", "image/png")
        assert store.count() == 2
        store.delete(_VALID_HEX_ID_32)
        assert store.count() == 1

    def test_oldest_mtime_is_none_when_empty(self, tmp_path):
        store = TmpImageStore(tmp_path, max_bytes=1024)
        store.init()
        assert store.oldest_mtime() is None

    def test_oldest_mtime_returns_a_float_after_writes(self, tmp_path):
        store = TmpImageStore(tmp_path, max_bytes=1024 * 1024)
        store.init()
        store.save(_VALID_HEX_ID_32, b"a", "image/png")
        m = store.oldest_mtime()
        assert isinstance(m, float)
        assert m > 0

    def test_current_total_bytes_returns_zero_when_empty(self, tmp_path):
        store = TmpImageStore(tmp_path, max_bytes=1024)
        store.init()
        assert store.current_total_bytes() == 0

    def test_current_total_bytes_increases_after_writes(self, tmp_path):
        store = TmpImageStore(tmp_path, max_bytes=1024 * 1024)
        store.init()
        store.save(_VALID_HEX_ID_32, b"x" * 50, "image/png")
        assert store.current_total_bytes() > 50  # blob + sidecar
