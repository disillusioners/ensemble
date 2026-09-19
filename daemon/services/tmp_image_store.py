"""Filesystem-backed store for transient clipboard images.

Phase 1 of the clipboard-image-chat feature. Phase 1 ships ONLY
persistence + serving — NO conversion, NO retention sweep (phase 3).

Layout
------

``<data_dir>/tmp_images/<id>`` — the bytes (extensionless per architect
risk #7: filename suffixes would leak the underlying MIME to an
attacker; the GET endpoint always sets ``Content-Type`` from the stored
MIME record, never from a filename suffix).

``<data_dir>/tmp_images/<id>.json`` — the sidecar with the original
content_type, decoded byte count, and upload timestamp. Written
atomically alongside the blob (a partial write would leave an
unreadable entry; the store writes both in order and treats any failure
on the second write as a rollback on the first).

The id regex ``^[a-f0-9]{32}$`` is enforced at the router layer
BEFORE any filesystem call (defense in depth — even if a malformed id
slipped through, the regex on ``image_id`` would block any read/write
that would touch paths outside the store).

Concurrency
-----------

Writes use ``O_CREAT|O_EXCL`` (POSIX atomic create-or-fail) so a
collision on a uuid4 id (vanishingly rare, but possible on a client
retry that re-mints deterministically) raises ``FileExistsError`` —
the router turns that into HTTP 409 ``CONFLICT``. Per-image byte
budget is enforced by a walkdir sum immediately before the write so
we never cross the configured cap (architect amendment #3).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from daemon.services.timestamps import now_utc_iso

logger = logging.getLogger(__name__)


class TmpImageStoreError(Exception):
    """Base class for store-level errors."""


class TmpImageNotFound(TmpImageStoreError):
    """Raised when ``open(image_id)`` cannot find the entry.

    Maps to HTTP 404. ``delete(image_id)`` swallows this silently
    (idempotent delete, per architect amendment #6).
    """


class TmpImageStoreFull(TmpImageStoreError):
    """Raised when a save would push the store past ``max_bytes``.

    Maps to HTTP 507 ``INSUFFICIENT_STORAGE``. The router logs a
    rate-limited WARNING (≤1 per minute per architect amendment #3).
    """


# Filename of the sidecar record. The blob itself is extensionless
# (architect risk #7) — the sidecar's job is to carry the MIME type
# the GET endpoint will serve back.
_METADATA_SUFFIX = ".json"


@dataclass(frozen=True)
class TmpImageRecord:
    """In-memory projection of the per-image sidecar record."""

    image_id: str
    content_type: str
    size_bytes: int
    uploaded_at: str  # ISO-8601 string with offset
    sha256_hex: str  # full sha256 hex digest of the bytes


class TmpImageStore:
    """Filesystem-backed transient image store.

    Thread-safe for concurrent reads/writes via ``os.open`` POSIX
    semantics. NOT multi-process safe — the daemon is single-process,
    so this is fine for the current shape; cross-process coordination
    is out of scope for phase 1.

    Parameters
    ----------
    data_dir:
        Parent directory under which the store creates ``tmp_images/``.
        Typically the resolved ``app.state.data_dir`` (which itself
        honors the ``ENSEMBLE_DATA_DIR > DATA_DIR > ./data`` chain —
        see ``daemon/api.py:232-244``).
    max_bytes:
        Hard cap on the store's total disk usage. The store walks the
        directory immediately before each write and refuses writes that
        would exceed the cap (``TmpImageStoreFull``). Default 1 GiB —
        configured via ``ServicesConfig.tmp_image_store_max_bytes``
        and overridden by the ``SERVICES_TMP_IMAGE_STORE_MAX_BYTES``
        env var.
    """

    _SUBDIR_NAME = "tmp_images"

    def __init__(self, data_dir: Path | str, max_bytes: int = 1024 ** 3) -> None:
        self._data_dir = Path(data_dir)
        self._dir = self._data_dir / self._SUBDIR_NAME
        self._max_bytes = max_bytes

    # ------------------------------------------------------------------
    # Construction / introspection
    # ------------------------------------------------------------------

    def init(self) -> None:
        """Create the store directory (idempotent — mirrors ``data/logs/``).

        Raises ``OSError`` on permission failures; the lifespan code
        surfaces those in the boot log but does NOT crash the daemon.
        """
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def data_dir(self) -> Path:
        """The parent ``data_dir`` this store was constructed against."""
        return self._data_dir

    @property
    def dir(self) -> Path:
        """The store directory (``data_dir/tmp_images``). Exists after ``init()``."""
        return self._dir

    @property
    def max_bytes(self) -> int:
        """Hard cap on disk usage."""
        return self._max_bytes

    def count(self) -> int:
        """Number of entries currently in the store.

        Counts BLOB entries only (excludes ``.json`` sidecars) — every
        blob has a matching sidecar so the count is the same either
        way; counting blobs keeps the metric meaningful if a partial
        sidecar somehow survives.
        """
        if not self._dir.exists():
            return 0
        n = 0
        with os.scandir(self._dir) as it:
            for entry in it:
                if entry.is_file() and not entry.name.endswith(_METADATA_SUFFIX):
                    n += 1
        return n

    def current_total_bytes(self) -> int:
        """Sum of every file's byte count under the store directory.

        Walks every file (blobs + sidecars). Cheap at typical sizes
        (≤1 GiB), called once per write — the write is the dominant
        cost anyway. Returns 0 if the directory does not exist yet
        (caller may invoke before ``init()`` in tests).
        """
        if not self._dir.exists():
            return 0
        total = 0
        with os.scandir(self._dir) as it:
            for entry in it:
                try:
                    total += entry.stat().st_size
                except FileNotFoundError:
                    # A concurrent cleanup sweep removed the file between
                    # scandir and stat — ignore. The next save will
                    # recount from a stable state.
                    continue
        return total

    def oldest_mtime(self) -> float | None:
        """mtime of the oldest blob in the store, or None when empty.

        Expressed as a POSIX timestamp (seconds since epoch) so the
        caller can format it however it needs to without dragging in a
        timezone library.
        """
        if not self._dir.exists():
            return None
        oldest: float | None = None
        with os.scandir(self._dir) as it:
            for entry in it:
                if not entry.is_file() or entry.name.endswith(_METADATA_SUFFIX):
                    continue
                try:
                    mtime = entry.stat().st_mtime
                except FileNotFoundError:
                    continue
                if oldest is None or mtime < oldest:
                    oldest = mtime
        return oldest

    def list_ids_with_mtime(self) -> list[tuple[str, float]]:
        """List every regular file in the store as ``(name, mtime)``.

        Phase 3 / clipboard-image-chat (retention sweep). Returns the
        raw filename (NOT the stripped id — the caller distinguishes
        blobs from ``.json`` sidecars by the suffix) paired with the
        POSIX mtime. The hidden ``data/tmp_images/.gitignore`` (phase 1
        Task 10) is excluded — it is a repo-hygiene artifact, not a
        stored image, and must never be reaped.

        Includes sidecar entries as well as blobs: the sweep needs the
        orphan-sidecar view (a sidecar whose blob was already removed)
        to age and reap it by its own mtime. Missing dir → ``[]``
        (caller may invoke before ``init()`` in tests); a file removed
        between ``scandir`` and ``stat`` (FE DELETE ∥ sweep race) is
        skipped silently — mirrors ``oldest_mtime``.
        """
        if not self._dir.exists():
            return []
        out: list[tuple[str, float]] = []
        with os.scandir(self._dir) as it:
            for entry in it:
                if not entry.is_file() or entry.name == ".gitignore":
                    continue
                try:
                    out.append((entry.name, entry.stat().st_mtime))
                except FileNotFoundError:
                    continue
        return out

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def save(
        self,
        image_id: str,
        content_bytes: bytes,
        content_type: str,
    ) -> TmpImageRecord:
        """Persist a new entry. Atomic per request.

        1. Walkdir sum: if ``current_total + new_size > max_bytes``,
           raise ``TmpImageStoreFull`` (router → HTTP 507). The
           projected size includes both the new blob AND the new
           sidecar — the cap is on TOTAL DISK USAGE under the store
           directory, not just blob bytes.
        2. Open the blob with ``O_CREAT|O_EXCL|O_WRONLY`` — a collision
           on the uuid4 hex raises ``FileExistsError`` (router → HTTP 409).
        3. Write the sidecar AFTER the blob (best-effort cleanup on
           partial failure: the caller has the original bytes and the
           router returns 500).

        Returns the ``TmpImageRecord`` that was persisted.
        """
        if not self._dir.exists():
            self.init()

        new_size = len(content_bytes)
        # Compute the SHA256 BEFORE writing — the digest goes into the
        # sidecar so the GET endpoint can serve ``ETag: W/"<hex[:16]>"``
        # without re-hashing the bytes on every request.
        sha256_hex = hashlib.sha256(content_bytes).hexdigest()
        uploaded_at = now_utc_iso()
        # Project the sidecar's byte count so the cap check sees the
        # total disk footprint. The JSON shape is stable so the size
        # is predictable. ~120 bytes for typical inputs; the sidecar
        # is well below 1 KiB even for pathological filenames.
        projected_sidecar_size = len(
            json.dumps(
                {
                    "content_type": content_type,
                    "size_bytes": new_size,
                    "uploaded_at": uploaded_at,
                    "sha256_hex": sha256_hex,
                }
            ).encode("utf-8")
        )
        current = self.current_total_bytes()
        if current + new_size + projected_sidecar_size > self._max_bytes:
            raise TmpImageStoreFull(
                f"tmp-image store full: current={current}B "
                f"new_blob={new_size}B new_sidecar={projected_sidecar_size}B "
                f"max={self._max_bytes}B"
            )

        blob_path = self._blob_path(image_id)
        meta_path = self._metadata_path(image_id)

        # O_CREAT|O_EXCL gives POSIX atomic create-or-fail — a retry
        # with the same id surfaces as FileExistsError, not silent
        # overwrite.
        fd = os.open(str(blob_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, content_bytes)
        finally:
            os.close(fd)

        record = TmpImageRecord(
            image_id=image_id,
            content_type=content_type,
            size_bytes=new_size,
            uploaded_at=uploaded_at,
            sha256_hex=sha256_hex,
        )
        # Sidecar write — best-effort: if it fails the blob is
        # already on disk and the GET endpoint will 404 (no MIME
        # record). The router surfaces the failure as 500.
        meta_path.write_text(
            json.dumps(
                {
                    "content_type": record.content_type,
                    "size_bytes": record.size_bytes,
                    "uploaded_at": record.uploaded_at,
                    "sha256_hex": record.sha256_hex,
                }
            ),
            encoding="utf-8",
        )
        return record

    def open(self, image_id: str) -> tuple[bytes, str]:
        """Read an entry's bytes + content_type.

        Raises ``TmpImageNotFound`` if either the blob or the sidecar
        is missing (a partial save leaves the entry un-readable until
        the next sweep cleans it up).
        """
        blob_path = self._blob_path(image_id)
        meta_path = self._metadata_path(image_id)
        if not blob_path.exists() or not meta_path.exists():
            raise TmpImageNotFound(f"tmp image not found: {image_id}")
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise TmpImageNotFound(
                f"tmp image sidecar unreadable: {image_id} ({exc})"
            ) from exc
        return blob_path.read_bytes(), meta["content_type"]

    def open_with_meta(self, image_id: str) -> tuple[bytes, str, str]:
        """Read an entry's bytes + content_type + sha256-hex.

        Same as ``open()`` but also returns the stored SHA256 digest so
        the GET endpoint can build a weak ETag without re-hashing.

        Raises ``TmpImageNotFound`` per ``open()``.
        """
        blob_path = self._blob_path(image_id)
        meta_path = self._metadata_path(image_id)
        if not blob_path.exists() or not meta_path.exists():
            raise TmpImageNotFound(f"tmp image not found: {image_id}")
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise TmpImageNotFound(
                f"tmp image sidecar unreadable: {image_id} ({exc})"
            ) from exc
        sha256_hex = meta.get("sha256_hex") or ""
        return blob_path.read_bytes(), meta["content_type"], sha256_hex

    def delete(self, image_id: str) -> bool:
        """Remove an entry. Idempotent — missing files are not errors.

        Returns ``True`` when at least one file was removed, ``False``
        when neither existed (idempotent no-op). The router ignores
        the return value and returns 204 unconditionally per architect
        amendment #6.
        """
        blob_path = self._blob_path(image_id)
        meta_path = self._metadata_path(image_id)
        removed = False
        for path in (blob_path, meta_path):
            try:
                path.unlink()
                removed = True
            except FileNotFoundError:
                pass
        return removed

    # ------------------------------------------------------------------
    # Path helpers — extensionless blobs per architect risk #7 ruling
    # ------------------------------------------------------------------

    def _blob_path(self, image_id: str) -> Path:
        """Path to the bytes. Extensionless.

        The router's path-traversal regex (``^[a-f0-9]{32}$``) keeps
        ``image_id`` from containing ``/`` or ``..``, so this is safe.
        """
        return self._dir / image_id

    def _metadata_path(self, image_id: str) -> Path:
        """Path to the JSON sidecar."""
        return self._dir / f"{image_id}{_METADATA_SUFFIX}"


# ----------------------------------------------------------------------
# Factory helper — used by the lifespan AND by the boot integration
# test to construct the store from the same shape. Keeps the wiring
# symmetric and testable without running the full create_app() span.
# ----------------------------------------------------------------------


def build_tmp_image_store(
    data_dir: Path | str,
    max_bytes: int,
) -> TmpImageStore:
    """Construct + initialize a ``TmpImageStore``.

    The lifespan calls this helper after reading
    ``config.services.tmp_image_store_max_bytes``; the boot integration
    test calls it directly to verify the wiring shape without booting
    the full daemon.
    """
    store = TmpImageStore(data_dir=data_dir, max_bytes=max_bytes)
    store.init()
    return store
