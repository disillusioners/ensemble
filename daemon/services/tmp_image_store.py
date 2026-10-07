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
atomically via a tmp-file + ``os.replace`` so a partial write never
leaves a torn sidecar observable on disk. The blob write is
``O_CREAT|O_EXCL``-atomic (POSIX create-or-fail); the sidecar write is
its own atomic step. A failure between the blob write and the
sidecar rename leaves the blob un-readable (the GET endpoint 404s on
the missing sidecar — no MIME record) until the next sweep cleans it
up; mid-batch rollback in ``save`` covers previously-written ids.

The id regex ``^[a-f0-9]{32}$`` is enforced at the router layer
BEFORE any filesystem call (defense in depth — even if a malformed id
slipped through, the regex on ``image_id`` would block any read/write
that would touch paths outside the store).

Concurrency
-----------

Writes are NOT atomic per request — the blob write is
``O_CREAT|O_EXCL``-atomic (POSIX create-or-fail, so a uuid4 collision
surfaces as ``FileExistsError`` → HTTP 409 ``CONFLICT``) but the
sidecar is its own atomic step (tmp-file + ``os.replace``). Per-image
byte budget is enforced by a walkdir sum immediately before the write
so we never cross the configured cap (architect amendment #3).
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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

# Phase 1 designer-agent extensions — WP7 (provenance) + WP9
# (retention class). Default values are intentionally the byte-identical
# state of the prior sidecar schema: with both extensions at their
# defaults, the persisted JSON is identical to a Phase 1 write.
_PROVENANCE_KEY = "provenance"
_RETENTION_CLASS_KEY = "retention_class"
_RETENTION_CLASS_NORMAL = "normal"
_RETENTION_CLASS_PROTECTED = "protected"
_VALID_RETENTION_CLASSES: frozenset[str] = frozenset(
    {_RETENTION_CLASS_NORMAL, _RETENTION_CLASS_PROTECTED}
)


@dataclass(frozen=True)
class TmpImageRecord:
    """In-memory projection of the per-image sidecar record.

    Phase 1 designer-agent extensions (WP7 + WP9):

    * ``provenance`` — optional ``{feature, page, version, source_agent}``
      mapping (any keys may be None; the mapping shape is whatever the
      caller passed). ``None`` on read when the sidecar is missing the
      key (the canonical clipboard path never sets it).
    * ``retention_class`` — ``"normal"`` (default, swept at 30 days)
      or ``"protected"`` (WP9: design baselines, exempt from the
      sweep). The cap still counts protected bytes — exhaustion
      raises ``TmpImageStoreFull``, no silent protected-eviction.

    Older sidecars written before these extensions (no
    ``provenance`` / no ``retention_class`` key) parse cleanly:
    ``provenance`` becomes ``None`` and ``retention_class`` becomes
    ``"normal"``. No migration, no rewrite — old bytes read
    unchanged.
    """

    image_id: str
    content_type: str
    size_bytes: int
    uploaded_at: str  # ISO-8601 string with offset
    sha256_hex: str  # full sha256 hex digest of the bytes
    provenance: dict[str, Any] | None = field(default=None)
    retention_class: str = _RETENTION_CLASS_NORMAL


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
        *,
        provenance: dict[str, Any] | None = None,
        retention_class: str = _RETENTION_CLASS_NORMAL,
    ) -> TmpImageRecord:
        """Persist a new entry.

        1. Walkdir sum: if ``current_total + new_size > max_bytes``,
           raise ``TmpImageStoreFull`` (router → HTTP 507). The
           projected size includes both the new blob AND the new
           sidecar — the cap is on TOTAL DISK USAGE under the store
           directory, not just blob bytes. Protected entries are
           counted the same way: the cap is a hard disk-usage budget,
           not an entry-count budget.
        2. Open the blob with ``O_CREAT|O_EXCL|O_WRONLY`` — a collision
           on the uuid4 hex raises ``FileExistsError`` (router → HTTP 409).
           The blob creation is POSIX-atomic and the payload is drained
           by a full-write loop, so a short ``os.write`` can never
           silently truncate the stored bytes (phase-1+3 review S2).
        3. Write the sidecar AFTER the blob. The sidecar write is its
           own atomic step (tmp-file + ``os.replace`` — see below). On
           any failure between steps 2 and 3, the blob stays on disk
           but the entry is un-readable (no MIME record → GET 404)
           until the next sweep reaps it. Mid-batch rollback in the
           router covers ids written earlier in the same batch; an
           in-progress failure is left to the sweep (architect §7).

        Phase 1 designer-agent extensions (WP7 / WP9) are ADDITIVE:

        * ``provenance`` — optional mapping (typically
          ``{feature, page, version, source_agent}``) written into the
          sidecar verbatim when provided. ``None`` (default) omits the
          key — clipboard-path callers get byte-identical sidecars and
          no migration is needed for existing entries.
        * ``retention_class`` — ``"normal"`` (default) or
          ``"protected"`` (WP9: design baselines, exempt from the
          30-day sweep). The cap check sees both classes identically.

        Returns the ``TmpImageRecord`` that was persisted.
        """
        if retention_class not in _VALID_RETENTION_CLASSES:
            raise ValueError(
                f"retention_class must be one of "
                f"{sorted(_VALID_RETENTION_CLASSES)!r}; got {retention_class!r}"
            )
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
        # is predictable. The optional extension keys
        # (``provenance`` / ``retention_class``) are included in the
        # projection when non-default so the cap check remains
        # accurate even on the extended-shape sidecars.
        sidecar_projection = self._build_sidecar_payload(
            content_type=content_type,
            size_bytes=new_size,
            uploaded_at=uploaded_at,
            sha256_hex=sha256_hex,
            provenance=provenance,
            retention_class=retention_class,
        )
        projected_sidecar_size = len(
            json.dumps(sidecar_projection).encode("utf-8")
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
        # overwrite. The payload itself is drained by a full-write
        # loop (phase-1+3 review S2): a short ``os.write`` must never
        # silently truncate the blob.
        fd = os.open(str(blob_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            view = memoryview(content_bytes)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    # Zero-progress write — raise rather than spin.
                    # For regular files os.write either completes or
                    # raises; this guard covers the pathological case.
                    raise OSError(
                        f"tmp-image blob write made no progress "
                        f"({len(view)} bytes remaining)"
                    )
                view = view[written:]
        finally:
            os.close(fd)

        record = TmpImageRecord(
            image_id=image_id,
            content_type=content_type,
            size_bytes=new_size,
            uploaded_at=uploaded_at,
            sha256_hex=sha256_hex,
            provenance=copy.deepcopy(provenance) if provenance else None,
            retention_class=retention_class,
        )
        # Sidecar write — atomic via tmp-file + ``os.replace`` so a
        # partial write is never observable. The tmp file is
        # namespaced by image_id (no collision risk across concurrent
        # writers for different ids). On any failure the tmp file is
        # best-effort unlinked; the blob stays on disk but the entry
        # is un-readable (no MIME record → GET 404) until the next
        # sweep reaps it.
        sidecar_payload = json.dumps(sidecar_projection)
        tmp_meta_path = meta_path.with_suffix(meta_path.suffix + ".tmp")
        try:
            tmp_meta_path.write_text(sidecar_payload, encoding="utf-8")
            os.replace(str(tmp_meta_path), str(meta_path))
        except Exception:
            # Best-effort cleanup of the tmp file so it never lingers
            # in the store dir after a failed write.
            try:
                tmp_meta_path.unlink()
            except FileNotFoundError:
                pass
            raise
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

    def stat_with_meta(
        self, image_id: str
    ) -> tuple[int, str, str]:
        """Read an entry's SIZE (via ``stat``) + content_type + sha.

        REWORK 2026-10-07 (m4): the prior shape called
        ``open_with_meta`` to populate the
        ``ResolvedTarget.size_bytes`` field at resolve time,
        then called it AGAIN at the router's GET/HEAD layer
        to actually read the bytes. Two blob reads per
        request, one of which (the first, at the resolve
        layer) was thrown away.

        ``stat_with_meta`` reports the size via ``stat`` (a
        no-read ``Path.stat()``) and the content_type +
        sha from the sidecar (a tiny JSON read). The
        expensive ``read_bytes`` happens once, at the
        router's GET path, on the byte return.

        Raises ``TmpImageNotFound`` per ``open()`` /
        ``open_with_meta`` so the same uniform 404 logic
        in the live-views router continues to fire.
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
        return blob_path.stat().st_size, meta["content_type"], sha256_hex

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
    # Designer-agent substrate — WP7 (provenance) + WP9 (retention)
    # ------------------------------------------------------------------

    def open_full(self, image_id: str) -> TmpImageRecord:
        """Read a full ``TmpImageRecord`` (provenance + retention_class).

        Same 404-gating as ``open()`` — a missing blob or unreadable
        sidecar raises ``TmpImageNotFound`` (the existing GET path).
        Old sidecars written before the extensions parse cleanly:
        ``provenance`` becomes ``None`` and ``retention_class`` becomes
        ``"normal"``.
        """
        blob_path = self._blob_path(image_id)
        meta_path = self._metadata_path(image_id)
        if not blob_path.exists() or not meta_path.exists():
            raise TmpImageNotFound(f"tmp image not found: {image_id}")
        meta = self._read_sidecar_json(meta_path, image_id)
        return self._record_from_sidecar(image_id, meta)

    def list_records(
        self,
        *,
        feature: str | None = None,
        page: str | None = None,
        version: str | None = None,
        source_agent: str | None = None,
        retention_class: str | None = None,
    ) -> list[TmpImageRecord]:
        """List ``TmpImageRecord`` rows matching the filter (AND-combined).

        Tolerates torn / missing sidecars via the same precedent as the
        sweep's orphan view (:215-221) — entries whose sidecar is
        missing, unreadable, or invalid JSON are EXCLUDED from the
        returned list (they look un-readable to a caller, by the same
        404-when-sidecar-missing contract the GET endpoint enforces).

        Filter combinations:

        * Each ``None`` key = no constraint on that field.
        * ``feature`` / ``page`` / ``version`` / ``source_agent`` are
          matched against the matching ``provenance`` key
          (``provenance["feature"] == feature``, etc.). A record
          whose ``provenance`` is ``None`` cannot match a non-``None``
          filter — the constraint is "I want rows tagged X", and
          rows with no tags at all are not tagged X.
        * ``retention_class`` is matched against the top-level
          ``retention_class`` field. ``None`` here means "any class"
          (the common listing case).

        Returns records sorted by ``uploaded_at`` ascending — the same
        chronological order the sweep views by, so callers get a
        stable iteration.
        """
        if not self._dir.exists():
            return []
        records: list[TmpImageRecord] = []
        for sidecar_name in self._iter_sidecar_names():
            image_id = sidecar_name[: -len(_METADATA_SUFFIX)]
            meta = self._read_sidecar_json_or_none(
                self._metadata_path(image_id), image_id
            )
            if meta is None:
                # Torn / missing / unreadable sidecar — excluded from
                # listing (404-gated). Sweep will reap it on the next
                # tick (a clean miss, never a partial record).
                continue
            record = self._record_from_sidecar(image_id, meta)
            if not self._record_matches(
                record,
                feature=feature,
                page=page,
                version=version,
                source_agent=source_agent,
                retention_class=retention_class,
            ):
                continue
            records.append(record)
        records.sort(key=lambda r: r.uploaded_at)
        return records

    def get_retention_class(self, image_id: str) -> str | None:
        """Return the stored ``retention_class`` for one id, or ``None``.

        ``None`` when:

        * the blob or sidecar is missing (the same 404-gated shape as
          ``open()`` — a torn entry reads as no-class);
        * the sidecar JSON is unreadable / invalid (defensive).

        Stored values are always normalized to ``"normal"`` or
        ``"protected"`` — anything else on disk is treated as
        ``"normal"`` to fail-open to the safe (sweep-eligible) class.
        """
        meta_path = self._metadata_path(image_id)
        meta = self._read_sidecar_json_or_none(meta_path, image_id)
        if meta is None:
            return None
        value = meta.get(_RETENTION_CLASS_KEY)
        if value == _RETENTION_CLASS_PROTECTED:
            return _RETENTION_CLASS_PROTECTED
        return _RETENTION_CLASS_NORMAL

    # ------------------------------------------------------------------
    # Internals — JSON shape + helpers shared by save/read paths
    # ------------------------------------------------------------------

    @staticmethod
    def _build_sidecar_payload(
        *,
        content_type: str,
        size_bytes: int,
        uploaded_at: str,
        sha256_hex: str,
        provenance: dict[str, Any] | None,
        retention_class: str,
    ) -> dict[str, Any]:
        """Compose the sidecar JSON dict for ``save``.

        Additive extensions (``provenance`` / ``retention_class``) are
        OMITTED from the payload when they are at their clipboard-path
        defaults — keeps the clipboard write byte-identical to the
        pre-WP7 sidecar shape so existing tests + readers parse
        unchanged.
        """
        payload: dict[str, Any] = {
            "content_type": content_type,
            "size_bytes": size_bytes,
            "uploaded_at": uploaded_at,
            "sha256_hex": sha256_hex,
        }
        if provenance is not None:
            payload[_PROVENANCE_KEY] = copy.deepcopy(provenance)
        if retention_class != _RETENTION_CLASS_NORMAL:
            payload[_RETENTION_CLASS_KEY] = retention_class
        return payload

    @staticmethod
    def _record_from_sidecar(
        image_id: str, meta: dict[str, Any]
    ) -> TmpImageRecord:
        """Project a sidecar dict into a ``TmpImageRecord``.

        Backward-compat defaults are applied for missing keys so old
        sidecars parse cleanly (the canonical clipboard-path write
        never sets the extension keys).
        """
        provenance_raw = meta.get(_PROVENANCE_KEY)
        if provenance_raw is None:
            provenance = None
        elif isinstance(provenance_raw, dict):
            provenance = copy.deepcopy(provenance_raw)
        else:
            # Malformed sidecar — coerce to None rather than echoing
            # the bad value back. A wrong-typed provenance is an old
            # bug we'll never re-introduce; defensively ignore.
            provenance = None
        retention_raw = meta.get(_RETENTION_CLASS_KEY)
        if retention_raw == _RETENTION_CLASS_PROTECTED:
            retention_class = _RETENTION_CLASS_PROTECTED
        else:
            # Default + defensive: any value other than the literal
            # "protected" string reads as "normal". This keeps a
            # tampered or unknown value from accidentally exempting
            # something from the sweep.
            retention_class = _RETENTION_CLASS_NORMAL
        return TmpImageRecord(
            image_id=image_id,
            content_type=str(meta.get("content_type", "")),
            size_bytes=int(meta.get("size_bytes", 0)),
            uploaded_at=str(meta.get("uploaded_at", "")),
            sha256_hex=str(meta.get("sha256_hex", "")),
            provenance=provenance,
            retention_class=retention_class,
        )

    @staticmethod
    def _record_matches(
        record: TmpImageRecord,
        *,
        feature: str | None,
        page: str | None,
        version: str | None,
        source_agent: str | None,
        retention_class: str | None,
    ) -> bool:
        if retention_class is not None and record.retention_class != retention_class:
            return False
        # Provenance-keyed filters. A record without provenance cannot
        # satisfy a non-None constraint for any provenance key.
        if record.provenance is None:
            return (
                feature is None
                and page is None
                and version is None
                and source_agent is None
            )
        if feature is not None and record.provenance.get("feature") != feature:
            return False
        if page is not None and record.provenance.get("page") != page:
            return False
        if version is not None and record.provenance.get("version") != version:
            return False
        if (
            source_agent is not None
            and record.provenance.get("source_agent") != source_agent
        ):
            return False
        return True

    def _iter_sidecar_names(self) -> list[str]:
        """List sidecar filenames in the store dir (stable order).

        Excludes the ``.gitignore`` repo-hygiene artifact (same
        precedent as ``list_ids_with_mtime``).
        """
        if not self._dir.exists():
            return []
        out: list[str] = []
        with os.scandir(self._dir) as it:
            for entry in it:
                if (
                    not entry.is_file()
                    or not entry.name.endswith(_METADATA_SUFFIX)
                    or entry.name == ".gitignore"
                ):
                    continue
                out.append(entry.name)
        out.sort()
        return out

    @staticmethod
    def _read_sidecar_json(meta_path: Path, image_id: str) -> dict[str, Any]:
        """Read + parse the sidecar JSON or raise ``TmpImageNotFound``.

        Mirrors ``open()``'s contract: missing files, missing parent
        blob, or unreadable JSON all surface as the same 404-style
        miss. Used for the ``open_full`` call site that requires
        strict 404 semantics.
        """
        try:
            raw = meta_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise TmpImageNotFound(
                f"tmp image sidecar missing: {image_id}"
            ) from exc
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, OSError) as exc:
            raise TmpImageNotFound(
                f"tmp image sidecar unreadable: {image_id} ({exc})"
            ) from exc
        if not isinstance(data, dict):
            raise TmpImageNotFound(
                f"tmp image sidecar has unexpected shape: {image_id}"
            )
        return data

    @staticmethod
    def _read_sidecar_json_or_none(
        meta_path: Path, image_id: str
    ) -> dict[str, Any] | None:
        """Read + parse the sidecar JSON; ``None`` on any miss.

        Used by ``list_records`` — torn / unreadable sidecars yield
        a clean None so the listing skips them (the orphan-view
        precedent). Mirrors the existing GET 404-on-missing-sidecar
        posture without raising.
        """
        if not meta_path.exists():
            return None
        try:
            raw = meta_path.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return data

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
