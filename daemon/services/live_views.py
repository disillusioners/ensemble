"""Live-view subsystem registry (Phase 1).

Owns the per-name → disk-path / policy mapping for the
``/views/<root>/<rel>`` route family. The registry is a thin
wrapper over the config-tree section ``live_views.roots`` plus a
``LiveViewsService`` that resolves a name + relative path into an
absolute on-disk path (and an optional content-type) WITH the full
path-traversal guard surface.

The registry is **read-only at request time** — it is populated
once at lifespan start from ``config.live_views.roots`` and
immutable thereafter. Adding a fourth root = config edit + restart
(``restart to flip`` matches the house ``live_views.*`` config
precedent set by ``tmp_image_store_max_bytes`` /
``tmp_image_cleanup_*``).

SECURITY HARD REQUIREMENTS (architect ruling, 2026-10-07):

* Path-traversal: reject ``..``, encoded ``%2e``, double-encoded
  ``%252e``, absolute path segments, null bytes, backslash tricks;
  enforce ``os.path.realpath`` containment within the resolved
  root dir (symlink escapes included).
* Read-only: ``GET/HEAD`` only — write/delete/list endpoints are
  NOT provided (the router exposes GET/HEAD exclusively).
* Content-Type via explicit extension→MIME map; NEVER sniff
  content; ``tmp-images`` MUST use sidecar MIME (not extension)
  per the architect ruling that already gates
  ``/api/tmp_images/<id>``.
* Unknown root-name / disabled / removed roots → uniform 404 with
  no path disclosure (no message that says "root exists but path
  missing" vs "root missing" — the caller can probe neither).

Three seed roots ship by default (config-driven; operator-editable):

* ``designer-artifact`` (project_scoped, REWORK 2026-10-07 M2) —
  the canonical
  ``<workdir>/.agents/shared/planning/<feature>/design/mockups/``
  subtree of the project named in the URL. URL shape
  ``/views/designer-artifact/<project_shortname>/<feature>/design/mockups/<file>``:
  the first URL segment after the root is the project shortname
  (anonymous-resolvable, exactly like ``planning``), and the
  rel must include the ``design/mockups`` subpath as a
  contiguous subsequence (REWORK M3, structural enforcement
  via the new ``required_rel_subpath`` config field). The
  pre-M2 design anchored the path on the calling instance's
  project workdir via ``request.state.instance_id``; nothing in
  the daemon ever set that attribute, so every anonymous
  browser hit on ``/views/designer-artifact/<rel>`` was a
  permanent 404. The project_scoped re-shape closes that gap
  by making the URL pattern carry the project locator.
* ``planning`` (project_scoped) — URL pattern
  ``/views/planning/<project_shortname>/<rel>`` resolves to
  ``<project_workdir>/.agents/shared/planning/<rel>``. This is
  the cross-project, project-aware reader; the first URL segment
  after the root is a project shortname, not a path component.
* ``tmp-images`` (tmp_images) — thin shim over the existing
  ``TmpImageStore`` substrate. MIME comes from the sidecar
  record, NOT the extension (architect risk #7 ruling).
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from daemon.config import LiveViewsConfig
    from daemon.services.tmp_image_store import TmpImageStore

logger = logging.getLogger(__name__)


# Root-name charset: kebab-case identifier, length 1..64, no
# leading/trailing hyphen, no underscores (hyphen-only matches the
# URL path convention for first-class daemon routes). The router
# also rejects anything that does not match this regex before any
# filesystem call.
_ROOT_NAME_REGEX = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")

# Maximum file size we'll ever serve. Soft cap — anything above
# this is 404'd to avoid serving large binaries through the daemon
# (the architect note: the route is for artifacts and short-lived
# text/markdown, not a generic file server).
_MAX_SERVED_BYTES: int = 32 * 1024 * 1024  # 32 MiB

# Minimal extension→MIME map for the Phase 1 surface. Anything
# not in this map returns ``application/octet-stream`` (the safe
# "I don't know" answer — the browser still renders the bytes
# correctly when the caller already knows the file shape, e.g. an
# agent that already fetched and previewed the artifact). Sniffing
# is FORBIDDEN (architect ruling — same as the tmp-images route).
_EXT_TO_MIME: dict[str, str] = {
    "html": "text/html; charset=utf-8",
    "htm": "text/html; charset=utf-8",
    "css": "text/css; charset=utf-8",
    "js": "application/javascript; charset=utf-8",
    "mjs": "application/javascript; charset=utf-8",
    "json": "application/json; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "markdown": "text/markdown; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "svg": "image/svg+xml; charset=utf-8",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "pdf": "application/pdf",
    "asc": "text/plain; charset=utf-8",  # designer's text-native wireframe
    "mmd": "text/plain; charset=utf-8",  # designer's text-native mermaid
    "xml": "application/xml; charset=utf-8",
    "yml": "application/yaml; charset=utf-8",
    "yaml": "application/yaml; charset=utf-8",
}


def _normalize_text(text: str) -> str:
    """NFKC-normalize + strip control chars before any regex check.

    Defense against unicode lookalikes that survive a 0x2E = '.'
    sanity check (e.g. fullwidth period '．' = U+FF0E). Returns
    the text in a form that ``str.lower()`` and the
    ``_ROOT_NAME_REGEX`` check both accept.
    """
    return unicodedata.normalize("NFKC", text).strip()


def is_well_formed_root_name(name: str) -> bool:
    """Return True iff ``name`` is a legal root-name token.

    Used by both the router (rejects bad names with a uniform 404
    before any resolver call) and the tool (rejects bad names with
    a typed error so the agent gets feedback).
    """
    if not name:
        return False
    normalized = _normalize_text(name)
    if normalized != name:
        # Any leading/trailing whitespace or unicode-folding
        # difference is a hard reject.
        return False
    return _ROOT_NAME_REGEX.match(name) is not None


def is_well_formed_rel_path(rel: str) -> bool:
    """Return True iff ``rel`` is a legal relative path token.

    A legal token has:

    * no null bytes
    * no control chars
    * no backslashes
    * no absolute segments (must not start with ``/``)
    * no encoded forms (``%2e``, ``%2f``, ``%00`` — the URL
      framework will decode percent-escapes before this check,
      so we only see the decoded form)
    * no ``..`` segments (POSIX and Windows both honor
      ``os.path.normpath`` semantics; we forbid them at the
      SEMENT level so a future encoder that re-encodes
      ``..`` cannot bypass the check)
    """
    if not rel:
        return False
    # Null bytes / control chars — REWORK 2026-10-07 (m3):
    # the C0 range (ord < 0x20) is rejected as before. The
    # DEL char (0x7F) and the C1 range (U+0080–U+009F) are
    # now ALSO rejected — they are control chars in disguise
    # that survive a regex check on printable ASCII but a
    # terminal or a misconfigured HTTP middlebox can render
    # them as escape sequences (e.g. CSI 0x9B). The
    # architectural rule is the same: the rel must be
    # printable, no control chars at all.
    if any(
        (ord(c) < 0x20)
        or (ord(c) == 0x7F)
        or (0x80 <= ord(c) <= 0x9F)
        for c in rel
    ):
        return False
    if "\\" in rel:
        return False
    if rel.startswith("/"):
        return False
    # The URL framework decodes percent-escapes before we see the
    # string, so the form below is the post-decode form. The
    # decoded form is what we validate. (A future router that
    # chose not to decode MUST apply this check to the encoded
    # form, but that is a router-level concern, not this one.)
    if ".." in PurePosixPath(rel).parts:
        return False
    return True


def extension_for(rel: str) -> str:
    """Return the lowercase extension WITHOUT leading dot, or ``""``.

    Examples::

        "foo/bar.html" -> "html"
        "foo/bar.HTML" -> "html"
        "foo/bar"      -> ""
        "foo/.hidden"  -> ""  (we treat dotfiles as no-ext)
        "foo/bar."     -> ""
    """
    name = os.path.basename(rel)
    _, ext = os.path.splitext(name)
    if not ext or ext == ".":
        return ""
    return ext.lstrip(".").lower()


def mime_for_extension(ext: str) -> str:
    """Return the explicit MIME for ``ext`` or the safe fallback.

    The map is small and CLOSED — anything not in ``_EXT_TO_MIME``
    returns ``application/octet-stream``. We never sniff content
    (architect ruling, mirrors the tmp-images path) AND we never
    consult the stdlib ``mimetypes`` registry: a host
    ``/etc/mime.types`` could map an agent-authored extension to
    ``text/html`` and the browser would execute the result on the
    daemon origin. The safe default for any unknown extension is
    ``application/octet-stream`` (the spec's allowed default).
    """
    if not ext:
        return "application/octet-stream"
    return _EXT_TO_MIME.get(ext, "application/octet-stream")


# ─────────────────────────────────────────────────────────────────
# Resolved-target dataclass
# ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ResolvedTarget:
    """A request that survived every traversal / isolation check.

    Carries the absolute on-disk path to read from, the
    content-type to advertise, the file size for the
    ``Content-Length`` header, and the root type so the router
    can branch on tmp-images semantics (sidecar-MIME) vs the
    filesystem path.
    """

    on_disk_path: Path
    content_type: str
    size_bytes: int
    root_type: str  # "filesystem" | "project_scoped" | "tmp_images"

    # REWORK 2026-10-07 (m5): ``raise_if_over_cap`` was dead
    # code — no caller ever invoked it (the router's
    # cap check is the service's ``size > _MAX_SERVED_BYTES``
    # at resolve time, and the post-M4 router's
    # ``_fd_read`` re-checks via fstat at the read layer).
    # The size cap survives as a service-level guard on
    # the resolve path AND as a router-level guard on the
    # read path; this dataclass just carries the metadata.


class LiveViewsServiceError(Exception):
    """Base class for resolution errors. All map to a uniform 404 in the router."""


class RootNotFoundError(LiveViewsServiceError):
    """The root name is unknown / disabled / removed."""


class TraversalError(LiveViewsServiceError):
    """A path-traversal attempt was rejected (encoded, ``..``, symlink escape)."""


class PathNotFoundError(LiveViewsServiceError):
    """The resolved path does not exist on disk."""


# ─────────────────────────────────────────────────────────────────
# Service
# ─────────────────────────────────────────────────────────────────


class LiveViewsService:
    """Resolves ``(root_name, rel_path)`` to a ``ResolvedTarget``.

    Construction is the only place that knows the per-app
    ``TmpImageStore`` (for the ``tmp-images`` root) and the
    per-instance project repository (for the project-scoped
    ``planning`` root). Once built, the service is a pure
    function of (root_name, rel_path, calling_instance_id).

    The instance_id parameter exists so the ``designer-artifact``
    root (filesystem type, but the path is relative to the
    project workdir) can be resolved against the active project.
    The ``planning`` root does the same via shortname lookup
    inside the URL itself, not via the instance context.
    """

    def __init__(
        self,
        *,
        config: "LiveViewsConfig",
        tmp_image_store: "TmpImageStore | None" = None,
        project_workdir_by_shortname_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self._config = config
        self._tmp_image_store = tmp_image_store
        # Resolver is passed in by the lifespan (avoids a hard
        # dependency on the manager here). It returns the project
        # main_directory (or None) for the supplied shortname.
        # None on unknown project / missing repo row. Only the
        # shortname variant survives — the per-instance
        # ``project_workdir_resolver`` was removed in the
        # REWORK 2026-10-07 (M2) when ``designer-artifact``
        # moved from filesystem-typed to project_scoped.
        self._project_workdir_by_shortname_resolver = (
            project_workdir_by_shortname_resolver
        )

    # ────────────────── public surface ──────────────────

    def enabled(self) -> bool:
        return self._config.enabled

    def root_names(self) -> list[str]:
        """Return the names of every registered root (for diagnostics)."""
        return sorted(self._config.roots.keys())

    def has_root(self, root_name: str) -> bool:
        """Return True iff ``root_name`` is registered AND enabled."""
        entry = self._config.roots.get(root_name)
        return bool(entry and entry.enabled)

    def is_known_root(self, root_name: str) -> bool:
        """Return True iff ``root_name`` is registered (regardless of enabled)."""
        return root_name in self._config.roots

    def resolve_for_instance(
        self,
        root_name: str,
        rel_path: str,
        *,
        calling_instance_id: str | None = None,
    ) -> ResolvedTarget:
        """Resolve a (root, rel) pair against the caller's project context.

        ``calling_instance_id`` is required for the
        ``designer-artifact`` root (which resolves relative to
        the caller's project workdir). For the ``tmp-images`` and
        ``planning`` roots the instance id is not consulted; the
        shortname in the URL is the project locator for
        ``planning``, and ``tmp-images`` keys on the URL id.
        """
        if not self._config.enabled:
            # Subsystem is OFF — uniform 404, never a partial.
            raise RootNotFoundError(root_name)

        if not is_well_formed_root_name(root_name):
            raise RootNotFoundError(root_name)

        entry = self._config.roots.get(root_name)
        if entry is None or not entry.enabled:
            raise RootNotFoundError(root_name)

        if not is_well_formed_rel_path(rel_path):
            raise TraversalError(rel_path)

        if entry.type == "tmp_images":
            return self._resolve_tmp_images(root_name, rel_path, entry)
        if entry.type == "project_scoped":
            return self._resolve_project_scoped(root_name, rel_path, entry)
        # filesystem (default) — works for designer-artifact
        return self._resolve_filesystem(
            root_name, rel_path, entry, calling_instance_id=calling_instance_id
        )

    # ────────────────── private resolvers ──────────────────

    def _resolve_filesystem(
        self,
        root_name: str,
        rel_path: str,
        entry,
        *,
        calling_instance_id: str | None,
    ) -> ResolvedTarget:
        if not entry.path:
            # Misconfiguration — the operator declared a filesystem
            # root without a path. Treat as unknown root (uniform
            # 404); no path disclosure.
            raise RootNotFoundError(root_name)

        # The filesystem type is rooted at an ABSOLUTE path
        # (``entry.path``). REWORK 2026-10-07 (M2): the
        # ``designer-artifact`` root is no longer filesystem-typed
        # — it is project_scoped now so the URL pattern
        # ``/views/designer-artifact/<shortname>/<rel>`` resolves
        # by shortname lookup, exactly like ``planning``.
        if entry.path and not Path(entry.path).is_absolute():
            # Refuse relative paths that did not get resolved by a
            # project context. An operator who configured a
            # filesystem root with a relative path will get a
            # uniform 404 — no auto-resolve against CWD (path
            # leak / test flake).
            raise RootNotFoundError(root_name)
        root_dir = Path(entry.path)

        return self._resolve_under_root(root_dir, rel_path, entry, root_type="filesystem")

    def _resolve_project_scoped(
        self,
        root_name: str,
        rel_path: str,
        entry,
    ) -> ResolvedTarget:
        # The URL pattern is:
        #   /views/<root>/<project_shortname>/<rel_path>
        # The first path segment after the root is the project
        # shortname; the rest is the relative path under the
        # project workdir's ``entry.path`` subdir.
        parts = [p for p in rel_path.split("/") if p]
        if not parts:
            # No project shortname supplied — uniform 404.
            raise RootNotFoundError(root_name)
        project_shortname, *rest = parts
        if not rest:
            # Only the shortname, no actual path — uniform 404.
            raise RootNotFoundError(root_name)
        sub_rel = "/".join(rest)

        if not is_well_formed_rel_path(sub_rel):
            raise TraversalError(sub_rel)

        # REWORK 2026-10-07 (M3): structural enforcement of a
        # required rel subpath. When the operator sets
        # ``required_rel_subpath`` on a project_scoped root, the
        # rel's ``/``-separated parts must contain that subpath
        # as a CONTIGUOUS subsequence. For ``designer-artifact``
        # the seeded subpath is ``['design', 'mockups']`` — a
        # rel like ``feat/design/mockups/landing.html`` passes
        # (``design``+``mockups`` are adjacent parts), but
        # ``feat/random.html`` 404s uniformly. This is the M3
        # closure: the operator cannot accidentally expose the
        # parent planning tree under a sub-scoped name.
        if entry.required_rel_subpath:
            required = entry.required_rel_subpath
            sub_parts = sub_rel.split("/")
            # Sliding window over sub_parts; require each element
            # of ``required`` to match in order, contiguously.
            # Refuse if any part of ``required`` is empty.
            if any(p == "" for p in required) or len(required) == 0:
                # Misconfiguration — uniform 404, no leak.
                raise RootNotFoundError(root_name)
            match = False
            for start in range(len(sub_parts) - len(required) + 1):
                if sub_parts[start:start + len(required)] == required:
                    match = True
                    break
            if not match:
                raise RootNotFoundError(root_name)

        if self._project_workdir_by_shortname_resolver is None:
            # Lifespan forgot to wire the shortname resolver —
            # treat as a missing project (uniform 404).
            raise RootNotFoundError(root_name)
        workdir = self._project_workdir_by_shortname_resolver(project_shortname)
        if not workdir:
            raise RootNotFoundError(root_name)

        if not entry.path:
            # Same misconfiguration handling as the filesystem
            # branch.
            raise RootNotFoundError(root_name)
        root_dir = Path(workdir) / entry.path
        return self._resolve_under_root(
            root_dir, sub_rel, entry, root_type="project_scoped"
        )

    def _resolve_tmp_images(
        self,
        root_name: str,
        rel_path: str,
        entry,
    ) -> ResolvedTarget:
        # The TmpImageStore is the substrate. ``rel_path`` is the
        # bare 32-hex image id; the store's stat_with_meta
        # (REWORK 2026-10-07 m4) returns ``(size, content_type,
        # sha)`` without reading the blob — the router layer
        # does the single read at request time. The sidecar
        # MIME is the only thing that survives from the
        # pre-M4 shape (architect risk #7 — NEVER
        # extension-guessed); the resolve is stat-based, not
        # blob-based, so the ``daemon/routers/tmp_images.py``
        # precedent only carries the MIME contract. A
        # ``/views/tmp-images/<id>`` URL and a
        # ``/api/tmp_images/<id>`` URL still serve the same
        # bytes with the same sidecar-MIME.
        if self._tmp_image_store is None:
            # The lifespan did not wire the store (unwritable
            # data dir, etc.) — uniform 404.
            raise RootNotFoundError(root_name)
        image_id = rel_path.strip()
        # Reject any shape the store would reject. Same regex
        # gate as the original /api/tmp_images router.
        if not re.match(r"^[a-f0-9]{32}$", image_id):
            raise RootNotFoundError(root_name)
        try:
            # REWORK 2026-10-07 (m4): use ``stat_with_meta`` so
            # the resolve layer does NOT read the blob bytes
            # (the prior ``open_with_meta`` call read the full
            # blob just to populate ``size_bytes``, then the
            # router's GET/HEAD layer read it again). The
            # ``stat`` is a no-read kernel call; the sidecar
            # is a small JSON read. The blob is read once, at
            # the router layer.
            size, content_type, _sha = self._tmp_image_store.stat_with_meta(
                image_id
            )
        except Exception as exc:
            # Any error from the store (TmpImageNotFound, torn
            # sidecar, OSError) maps to a uniform 404.
            logger.debug(
                "[LiveViews] tmp-images miss for %s: %s", image_id, exc
            )
            raise RootNotFoundError(root_name) from exc

        # The store's blob is extensionless; we synthesize an
        # "on_disk_path" for the response shape (the router
        # doesn't read it; the dataclass is informational).
        return ResolvedTarget(
            on_disk_path=self._tmp_image_store.dir / image_id,
            content_type=content_type,
            size_bytes=size,
            root_type="tmp_images",
        )

    # ────────────────── tmp-images public open (REWORK m4) ──────────────────

    def open_tmp_image(self, image_id: str) -> tuple[bytes, str, str] | None:
        """Read the bytes + content_type + sha for a tmp-image id.

        REWORK 2026-10-07 (m4): the router previously reached
        into ``self._tmp_image_store.open_with_meta`` (a
        private-attr access from outside the service). The
        public method on the service is the same wire with a
        name that survives grep + review. Returns ``None``
        on any store error (the router collapses ``None`` to
        the uniform 404). The store is the one that owns the
        read; the service is the seam the router reaches.
        """
        if self._tmp_image_store is None:
            return None
        try:
            return self._tmp_image_store.open_with_meta(image_id)
        except Exception as exc:
            logger.debug(
                "[LiveViews] tmp-images open miss for %s: %s",
                image_id,
                exc,
            )
            return None

    # ────────────────── shared containment check ──────────────────

    def _resolve_under_root(
        self,
        root_dir: Path,
        rel_path: str,
        entry,
        *,
        root_type: str,
    ) -> ResolvedTarget:
        """Containment-resolve a relative path under ``root_dir``.

        The sequence is the architect-ruled layered guard:

        1. ``is_well_formed_rel_path`` already filtered the input
           shape (``..`` segments, control chars, leading ``/``,
           backslashes).
        2. ``root_dir.resolve(strict=False)`` followed by
           ``(root_dir / rel_path).resolve(strict=False)`` so a
           symlink inside the root cannot point outside the
           resolved root boundary. If the resolved candidate
           does not start with the resolved root, REJECT.
        3. Allowlist check on the file extension when the root
           declares ``allowed_extensions`` (None = no gate).
        4. Existence check: missing file → 404 (uniform miss).
        5. Read as a regular file (refuse directories / sockets
           / fifos / device files — those 404 too).
        """
        # Containment under the resolved root_dir:
        root_resolved = root_dir.resolve()
        candidate = (root_resolved / rel_path).resolve()
        try:
            candidate.relative_to(root_resolved)
        except ValueError as exc:
            # Symlink escape (or any other ``..`` that slipped
            # past the shape check) — uniform 404.
            raise TraversalError(rel_path) from exc

        # Allowlist (optional):
        if entry.allowed_extensions is not None:
            ext = extension_for(rel_path)
            if ext not in entry.allowed_extensions:
                # Extension not in the allowlist — uniform 404
                # (the file may exist; we just don't serve it).
                raise PathNotFoundError(rel_path)

        # Existence + regular-file check:
        if not candidate.is_file():
            raise PathNotFoundError(rel_path)

        size = candidate.stat().st_size
        if size > _MAX_SERVED_BYTES:
            # Soft cap — uniform miss, no path-disclosure error
            # message.
            raise PathNotFoundError(rel_path)

        mime = mime_for_extension(extension_for(rel_path))
        return ResolvedTarget(
            on_disk_path=candidate,
            content_type=mime,
            size_bytes=size,
            root_type=root_type,
        )

    # ────────────────── URL minting ──────────────────

    def build_url(self, root_name: str, rel_path: str) -> str | None:
        """Return a canonical URL for ``(root_name, rel_path)`` or None.

        None = the root is unknown / disabled / subsystem off.
        The tool surface renders None as an ``Error: ...`` so
        the agent gets a typed rejection (never a partial URL).

        The URL shape:

        * ``/views/<root>/<rel>`` when ``external_base_url`` is
          unset (path-relative, the recommended default — the
          daemon has no public hostname).
        * ``<external_base_url>/views/<root>/<rel>`` when set.

        ``rel_path`` is NOT URL-encoded here — it must be a
        SAFE, already-resolvable path. The agent supplies it.
        The router / store resolve it later; a bad agent-supplied
        path produces a 404 on the access side, never a broken
        link.
        """
        if not self._config.enabled:
            return None
        if not self.has_root(root_name):
            return None
        if not rel_path:
            return None
        if not is_well_formed_root_name(root_name):
            return None
        if not is_well_formed_rel_path(rel_path):
            return None
        path_part = f"/views/{root_name}/{rel_path}"
        if self._config.external_base_url:
            base = self._config.external_base_url.rstrip("/")
            return f"{base}{path_part}"
        return path_part
