"""Unit tests for ``daemon.services.live_views`` (Phase 1).

Covers the user's mandatory list:

* Path-traversal attempts rejected (encoded + double-encoded +
  absolute + null bytes + symlink escape).
* Root isolation (root name + path can't escape root dir).
* Content-type correctness (allowed extensions + tmp-images
  sidecar-MIME; unknown → application/octet-stream or 404 per
  policy).
* Link minting (URL shape via service.build_url; base-URL vs
  path-relative).
* Registry behavior (resolve by name; unknown root → uniform
  404; enabled/disabled semantics).

The service is constructed with ``tmp_image_store=None`` etc.
for the registry-shape tests, and a real ``TmpImageStore`` for
the tmp-images root tests. The project-scoped resolvers are
passed per-test (the ``_service`` helper threads a
``{"shortname": workdir}`` map in). Symlink-escape tests use
``tmp_path`` + an OS-level ``os.symlink`` (POSIX-only —
skipped on Windows per the tmp_image_store precedent).
"""

from __future__ import annotations

import errno
import os
import pathlib

import pytest

from daemon.config import LiveViewsConfig, LiveViewsRootConfig
from daemon.services.live_views import (
    LiveViewsService,
    PathNotFoundError,
    RootNotFoundError,
    TraversalError,
    _EXT_TO_MIME,
    extension_for,
    is_well_formed_rel_path,
    is_well_formed_root_name,
    mime_for_extension,
)
from daemon.services.tmp_image_store import TmpImageStore


# ===========================================================================
# Group 1 — Shape guards: is_well_formed_root_name
# ===========================================================================


class TestRootNameShape:
    """``is_well_formed_root_name`` is the first line of defense.

    The router / tool apply this BEFORE any resolver call;
    a bad name collapses to ``RootNotFoundError`` (uniform 404
    envelope) without touching the filesystem.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "designer-artifact",
            "planning",
            "tmp-images",
            "docs",
            "a",
            "abc-123",
            "x" * 64,  # max length
        ],
    )
    def test_accepts_well_formed(self, name: str):
        assert is_well_formed_root_name(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "",  # empty
            "-leading-hyphen",  # leading hyphen forbidden
            "trailing-hyphen-",  # trailing hyphen forbidden
            "Has_Underscore",  # underscore forbidden
            "Has Space",  # whitespace forbidden
            "ALL_CAPS_BAD",  # uppercase forbidden (kebab-case only)
            "x" * 65,  # too long
            "with.dot",  # dot forbidden
            "with/slash",  # slash forbidden
            "with\\backslash",  # backslash forbidden
            "with:colon",  # colon forbidden
            "with?question",  # punctuation forbidden
            "résumé",  # unicode forbidden
        ],
    )
    def test_rejects_malformed(self, name: str):
        assert is_well_formed_root_name(name) is False


# ===========================================================================
# Group 2 — Shape guards: is_well_formed_rel_path
# ===========================================================================


class TestRelPathShape:
    """``is_well_formed_rel_path`` is the second line of defense.

    The check operates on the POST-DECODE form (FastAPI decodes
    percent-escapes before the route handler runs), so the
    rejected set is the post-decode shape.
    """

    @pytest.mark.parametrize(
        "rel",
        [
            "index.html",
            "foo/bar/baz.html",
            "a",
            "foo/bar.baz/qux",  # dot in dir, not in extension
            "foo-bar_baz.html",  # hyphens + underscores in name are fine
        ],
    )
    def test_accepts_well_formed(self, rel: str):
        assert is_well_formed_rel_path(rel) is True

    @pytest.mark.parametrize(
        "rel",
        [
            "",  # empty
            "/leading-slash",  # absolute
            "../escape",  # traversal
            "foo/../escape",  # traversal in middle
            "foo/..",  # trailing traversal
            "..",  # bare traversal
            "foo\x00bar",  # null byte
            "foo\x01bar",  # control char (C0)
            "foo\x7Fbar",  # DEL (REWORK 2026-10-07 m3)
            "foo\x80bar",  # C1 lower bound
            "foo\x9Fbar",  # C1 upper bound
            "foo\x9Bbar",  # C1 CSI
            "foo\\bar",  # backslash
        ],
    )
    def test_rejects_malformed(self, rel: str):
        assert is_well_formed_rel_path(rel) is False


# ===========================================================================
# Group 3 — Content-type map
# ===========================================================================


class TestContentType:
    """``mime_for_extension`` is the explicit-extension→MIME map.

    No content sniffing (architect ruling). Unknown extensions
    return ``application/octet-stream`` (the safe "I don't know"
    answer). The map is closed and intentional — adding a new
    common artifact extension is a small, documented change.
    """

    @pytest.mark.parametrize(
        "ext,mime",
        [
            ("html", "text/html; charset=utf-8"),
            ("md", "text/markdown; charset=utf-8"),
            ("json", "application/json; charset=utf-8"),
            ("png", "image/png"),
            ("svg", "image/svg+xml; charset=utf-8"),
            ("pdf", "application/pdf"),
        ],
    )
    def test_known_extension(self, ext: str, mime: str):
        assert mime_for_extension(ext) == mime

    def test_unknown_extension_returns_octet_stream(self):
        # Pick an extension the stdlib mimetypes registry does
        # NOT recognize (and that is not in our closed map).
        # "qqqq" is intentionally absurd — neither in the map
        # nor in the stdlib registry.
        assert mime_for_extension("qqqq") == "application/octet-stream"

    def test_stdlib_fallback_for_known_stdlib_extension(self):
        # The service uses a CLOSED extension→MIME map. A
        # stdlib-recognised extension that is NOT in our closed
        # map (e.g. "csv") must fall to ``application/octet-stream``
        # — we never consult the host ``/etc/mime.types`` registry,
        # because that would let an operator / packager map an
        # agent-authored extension to ``text/html`` and the browser
        # would execute the result on the daemon origin. The safe
        # default for any unknown extension is the spec's allowed
        # ``application/octet-stream``.
        assert mime_for_extension("csv") == "application/octet-stream"

    def test_empty_extension_returns_octet_stream(self):
        assert mime_for_extension("") == "application/octet-stream"

    def test_extension_for_strips_dot(self):
        assert extension_for("foo/bar.HTML") == "html"

    def test_extension_for_dotfile_returns_empty(self):
        # ".hidden" is treated as a dotfile with no extension
        # (the leading dot is the dotfile marker, not a separator).
        assert extension_for("foo/.hidden") == ""

    def test_extension_for_no_extension(self):
        assert extension_for("foo/bar") == ""

    def test_ext_to_mime_is_closed_map(self):
        # The map should NOT be empty — verify the seed set is
        # in place. Adding a new extension is intentional and
        # a PR-time change.
        assert "html" in _EXT_TO_MIME
        assert "png" in _EXT_TO_MIME
        assert "md" in _EXT_TO_MIME


# ===========================================================================
# Group 4 — Registry behavior (filesystem root)
# ===========================================================================


class TestPhase1SeedDefaults:
    """Acceptance test (review follow-up MAJOR, 2026-10-07).

    Building the service from a default ``LiveViewsConfig()`` (no
    operator config, no env vars) + a ``TmpImageStore`` MUST
    register exactly the three Phase-1 roots by name and bind the
    ``tmp-images`` root to the live store's directory.

    The config path remains the authority when present: setting
    a single root's ``enabled: False`` removes ONLY that root
    from the resolvable set (the other seeds stay).
    """

    def test_default_config_registers_three_phase1_roots(self):
        # No operator config: a default ``LiveViewsConfig()``
        # must already carry the three Phase-1 seed roots. The
        # service is request-only — building it is cheap; the
        # assertion is on the registry state.
        cfg = LiveViewsConfig()
        assert set(cfg.roots.keys()) == {
            "designer-artifact",
            "planning",
            "tmp-images",
        }

    def test_seed_designer_artifact_is_project_scoped_with_mockups_gate(self):
        # REWORK 2026-10-07 (M2+M3): the designer-artifact seed
        # is project_scoped (anonymous-resolvable by shortname)
        # and carries the ``required_rel_subpath`` gate that
        # enforces the canonical mockups subtree shape by
        # construction. The old filesystem-against-calling-instance
        # wiring was a permanent 404 for anonymous browser hits
        # (M2 finding).
        cfg = LiveViewsConfig()
        da = cfg.roots["designer-artifact"]
        assert da.type == "project_scoped"
        assert da.required_rel_subpath == ["design", "mockups"]

    def test_service_root_names_match_phase1(self, tmp_path: pathlib.Path):
        # Building the service with a default config + a live
        # TmpImageStore registers exactly the three names.
        store = TmpImageStore(tmp_path, max_bytes=10 * 1024 * 1024)
        store.init()
        cfg = LiveViewsConfig()
        svc = LiveViewsService(config=cfg, tmp_image_store=store)
        assert svc.root_names() == [
            "designer-artifact",
            "planning",
            "tmp-images",
        ]
        # And every seed is enabled (resolvable).
        assert svc.has_root("designer-artifact") is True
        assert svc.has_root("planning") is True
        assert svc.has_root("tmp-images") is True

    def test_tmp_images_root_path_binds_to_store_dir(
        self, tmp_path: pathlib.Path
    ):
        # The tmp-images root's resolved path equals the live
        # store's directory (no separate ``path`` config knob —
        # the bind is at request time, via ``store.dir``).
        store = TmpImageStore(tmp_path, max_bytes=10 * 1024 * 1024)
        store.init()
        cfg = LiveViewsConfig()
        svc = LiveViewsService(config=cfg, tmp_image_store=store)
        # Save + resolve to a known image id; the on_disk_path
        # parent is the store dir.
        rec = store.save(
            image_id="abcdef0123456789abcdef0123456789",
            content_bytes=b"X",
            content_type="text/plain",
        )
        resolved = svc.resolve_for_instance("tmp-images", rec.image_id)
        assert resolved.on_disk_path.parent == store.dir

    def test_disabling_a_seed_root_removes_only_that_root(
        self, tmp_path: pathlib.Path
    ):
        # Operator-override model: writing a config block with a
        # single root's ``enabled: False`` removes ONLY that root
        # from the resolvable set. The other two seeds stay.
        # (In practice the operator writes a full roots block —
        # this test pins the same behavior by mutating the
        # seeded entry directly, which is what pydantic does
        # when it constructs a config with operator-supplied
        # init kwargs.)
        store = TmpImageStore(tmp_path, max_bytes=10 * 1024 * 1024)
        store.init()
        cfg = LiveViewsConfig()
        cfg.roots["tmp-images"] = LiveViewsRootConfig(
            type="tmp_images", enabled=False
        )
        svc = LiveViewsService(config=cfg, tmp_image_store=store)
        # The other two seeds are still resolvable.
        assert svc.has_root("designer-artifact") is True
        assert svc.has_root("planning") is True
        # The disabled root is still KNOWN (operator-override
        # model keeps the name) but NOT resolvable.
        assert svc.is_known_root("tmp-images") is True
        assert svc.has_root("tmp-images") is False


class TestFilesystemRootRegistry:
    """The ``filesystem`` root type. Resolved against an absolute
    operator-supplied path; relative paths rejected.
    """

    def test_unknown_root_returns_root_not_found(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig()
        cfg.roots["known"] = LiveViewsRootConfig(
            type="filesystem", path=str(tmp_path)
        )
        svc = LiveViewsService(config=cfg)
        with pytest.raises(RootNotFoundError):
            svc.resolve_for_instance("unknown", "foo")

    def test_disabled_root_returns_root_not_found(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig()
        cfg.roots["off"] = LiveViewsRootConfig(
            type="filesystem", path=str(tmp_path), enabled=False
        )
        svc = LiveViewsService(config=cfg)
        with pytest.raises(RootNotFoundError):
            svc.resolve_for_instance("off", "foo")

    def test_subsystem_disabled_returns_root_not_found(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig(enabled=False)
        cfg.roots["docs"] = LiveViewsRootConfig(type="filesystem", path=str(tmp_path))
        svc = LiveViewsService(config=cfg)
        with pytest.raises(RootNotFoundError):
            svc.resolve_for_instance("docs", "foo")

    def test_filesystem_root_with_relative_path_rejected(self, tmp_path: pathlib.Path):
        # The operator must supply an absolute path for non-
        # ``designer-artifact`` filesystem roots; relative paths
        # are a config error that maps to a uniform 404.
        cfg = LiveViewsConfig()
        cfg.roots["bad"] = LiveViewsRootConfig(
            type="filesystem", path="./relative"
        )
        svc = LiveViewsService(config=cfg)
        with pytest.raises(RootNotFoundError):
            svc.resolve_for_instance("bad", "foo")

    def test_happy_path_resolves_under_root(self, tmp_path: pathlib.Path):
        # Create a real file and resolve through the service.
        target = tmp_path / "foo" / "bar.html"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"<h1>hi</h1>")
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(type="filesystem", path=str(tmp_path))
        svc = LiveViewsService(config=cfg)
        resolved = svc.resolve_for_instance("docs", "foo/bar.html")
        assert resolved.size_bytes == len(b"<h1>hi</h1>")
        assert resolved.content_type == "text/html; charset=utf-8"
        assert resolved.on_disk_path == target

    def test_missing_file_returns_path_not_found(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(type="filesystem", path=str(tmp_path))
        svc = LiveViewsService(config=cfg)
        with pytest.raises(PathNotFoundError):
            svc.resolve_for_instance("docs", "no-such-file.html")

    def test_directory_not_a_file_returns_path_not_found(self, tmp_path: pathlib.Path):
        (tmp_path / "subdir").mkdir()
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(type="filesystem", path=str(tmp_path))
        svc = LiveViewsService(config=cfg)
        with pytest.raises(PathNotFoundError):
            svc.resolve_for_instance("docs", "subdir")


# ===========================================================================
# Group 5 — Path-traversal guard matrix
# ===========================================================================


class TestTraversalGuard:
    """The user-mandated path-traversal matrix.

    Every entry must raise (TraversalError or RootNotFoundError) —
    uniform 404 envelope, never a leak. The shape guard rejects
    most cases BEFORE the filesystem call; the containment check
    is the second layer for symlink escapes that survive the
    shape guard.
    """

    def test_dotdot_segment_rejected(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(type="filesystem", path=str(tmp_path))
        svc = LiveViewsService(config=cfg)
        with pytest.raises(TraversalError):
            svc.resolve_for_instance("docs", "../etc/passwd")

    def test_dotdot_in_middle_rejected(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(type="filesystem", path=str(tmp_path))
        svc = LiveViewsService(config=cfg)
        with pytest.raises(TraversalError):
            svc.resolve_for_instance("docs", "foo/../../etc/passwd")

    def test_dotdot_at_end_rejected(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(type="filesystem", path=str(tmp_path))
        svc = LiveViewsService(config=cfg)
        with pytest.raises(TraversalError):
            svc.resolve_for_instance("docs", "foo/..")

    def test_bare_dotdot_rejected(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(type="filesystem", path=str(tmp_path))
        svc = LiveViewsService(config=cfg)
        with pytest.raises(TraversalError):
            svc.resolve_for_instance("docs", "..")

    def test_leading_slash_rejected(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(type="filesystem", path=str(tmp_path))
        svc = LiveViewsService(config=cfg)
        with pytest.raises(TraversalError):
            svc.resolve_for_instance("docs", "/etc/passwd")

    def test_null_byte_rejected(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(type="filesystem", path=str(tmp_path))
        svc = LiveViewsService(config=cfg)
        with pytest.raises(TraversalError):
            svc.resolve_for_instance("docs", "foo\x00bar.html")

    def test_control_char_rejected(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(type="filesystem", path=str(tmp_path))
        svc = LiveViewsService(config=cfg)
        with pytest.raises(TraversalError):
            svc.resolve_for_instance("docs", "foo\x01bar.html")

    def test_backslash_rejected(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(type="filesystem", path=str(tmp_path))
        svc = LiveViewsService(config=cfg)
        with pytest.raises(TraversalError):
            svc.resolve_for_instance("docs", "foo\\bar.html")

    @pytest.mark.skipif(os.name == "nt", reason="POSIX symlink only")
    def test_symlink_escape_rejected(self, tmp_path: pathlib.Path):
        # A symlink INSIDE the root that points OUTSIDE the root
        # must be caught by the realpath containment check. The
        # shape guard passes (the link target looks like a normal
        # relative path under the root) — the containment check
        # fires.
        outside = tmp_path.parent / "outside-secret.txt"
        outside.write_text("SECRET")
        link = tmp_path / "link.html"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("symlink not supported here")
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(type="filesystem", path=str(tmp_path))
        svc = LiveViewsService(config=cfg)
        # Containment check fires — TraversalError is the
        # containment check's signature (the shape check would
        # not have caught this).
        with pytest.raises((TraversalError, PathNotFoundError)):
            svc.resolve_for_instance("docs", "link.html")


# ===========================================================================
# Group 6 — Root isolation (per-root enforcement)
# ===========================================================================


class TestRootIsolation:
    """Each root's resolved directory is its own boundary.

    A symlink from one root's tree to another root's tree is
    caught by the per-root containment check. A path that
    resolves into a different root's tree is rejected.
    """

    def test_two_filesystem_roots_are_isolated(self, tmp_path: pathlib.Path):
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        root_a.mkdir()
        root_b.mkdir()
        (root_a / "shared.html").write_text("A")
        (root_b / "shared.html").write_text("B")

        cfg = LiveViewsConfig()
        cfg.roots["a"] = LiveViewsRootConfig(type="filesystem", path=str(root_a))
        cfg.roots["b"] = LiveViewsRootConfig(type="filesystem", path=str(root_b))
        svc = LiveViewsService(config=cfg)

        ra = svc.resolve_for_instance("a", "shared.html")
        rb = svc.resolve_for_instance("b", "shared.html")
        assert ra.on_disk_path == root_a / "shared.html"
        assert rb.on_disk_path == root_b / "shared.html"

    @pytest.mark.skipif(os.name == "nt", reason="POSIX symlink only")
    def test_symlink_into_another_root_rejected(self, tmp_path: pathlib.Path):
        root_a = tmp_path / "a"
        root_b = tmp_path / "b"
        root_a.mkdir()
        root_b.mkdir()
        (root_b / "secret.html").write_text("SECRET")
        # Create a symlink in root A pointing into root B.
        link = root_a / "leak.html"
        try:
            link.symlink_to(root_b / "secret.html")
        except (OSError, NotImplementedError):
            pytest.skip("symlink not supported here")
        cfg = LiveViewsConfig()
        cfg.roots["a"] = LiveViewsRootConfig(type="filesystem", path=str(root_a))
        svc = LiveViewsService(config=cfg)
        # Containment check: link.html's realpath is root_b, not
        # root_a. The service rejects.
        with pytest.raises((TraversalError, PathNotFoundError)):
            svc.resolve_for_instance("a", "leak.html")


# ===========================================================================
# Group 7 — Extension allowlist
# ===========================================================================


class TestExtensionAllowlist:
    """The root-level ``allowed_extensions`` filter.

    When set, only files whose extension is in the list resolve.
    When unset (None), every extension passes. The filter is the
    safe-by-default way to constrain a filesystem root to
    a closed artifact set (e.g. only HTML for the docs root).
    """

    def test_extension_in_allowlist_serves(self, tmp_path: pathlib.Path):
        (tmp_path / "a.html").write_text("A")
        (tmp_path / "a.exe").write_text("BAD")
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(
            type="filesystem",
            path=str(tmp_path),
            allowed_extensions=["html"],
        )
        svc = LiveViewsService(config=cfg)
        # html is in the allowlist — resolves.
        resolved = svc.resolve_for_instance("docs", "a.html")
        assert resolved.content_type == "text/html; charset=utf-8"
        # exe is NOT — uniform 404 (no info leak).
        with pytest.raises(PathNotFoundError):
            svc.resolve_for_instance("docs", "a.exe")

    def test_extension_allowlist_case_insensitive(self, tmp_path: pathlib.Path):
        (tmp_path / "a.HTML").write_text("A")
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(
            type="filesystem",
            path=str(tmp_path),
            allowed_extensions=["html"],
        )
        svc = LiveViewsService(config=cfg)
        # ``.HTML`` lowercased to ``html`` → allowed.
        resolved = svc.resolve_for_instance("docs", "a.HTML")
        assert resolved.content_type == "text/html; charset=utf-8"

    def test_no_allowlist_serves_everything(self, tmp_path: pathlib.Path):
        (tmp_path / "a.qqqq").write_text("weird")
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(type="filesystem", path=str(tmp_path))
        svc = LiveViewsService(config=cfg)
        resolved = svc.resolve_for_instance("docs", "a.qqqq")
        # Unknown extension with no allowlist → application/
        # octet-stream (safe fallback — never sniff).
        assert resolved.content_type == "application/octet-stream"


# ===========================================================================
# Group 8 — tmp-images root (delegates to TmpImageStore)
# ===========================================================================


class TestTmpImagesRoot:
    """The ``tmp_images`` root delegates entirely to the per-app
    ``TmpImageStore`` with the same regex gate the /api/tmp_images
    router applies. MIME comes from the SIDECAR, never the
    extension (architect risk #7).
    """

    def _service_with_store(
        self, store: TmpImageStore
    ) -> LiveViewsService:
        cfg = LiveViewsConfig()
        cfg.roots["tmp-images"] = LiveViewsRootConfig(type="tmp_images")
        return LiveViewsService(config=cfg, tmp_image_store=store)

    def test_happy_path_resolves_with_sidecar_mime(
        self, tmp_path: pathlib.Path
    ):
        store = TmpImageStore(tmp_path, max_bytes=10 * 1024 * 1024)
        store.init()
        # Sidecar-MIME wins over extension: store as text/plain
        # with image_id that does NOT carry a .html suffix.
        rec = store.save(
            image_id="1234567890abcdef1234567890abcdef",
            content_bytes=b"Hello",
            content_type="text/plain",
        )
        svc = self._service_with_store(store)
        resolved = svc.resolve_for_instance("tmp-images", rec.image_id)
        assert resolved.content_type == "text/plain"
        assert resolved.size_bytes == 5
        assert resolved.root_type == "tmp_images"

    def test_malformed_id_returns_root_not_found(
        self, tmp_path: pathlib.Path
    ):
        store = TmpImageStore(tmp_path, max_bytes=10 * 1024 * 1024)
        store.init()
        svc = self._service_with_store(store)
        # 32 chars but not hex — same regex gate as the HTTP
        # /api/tmp_images router applies.
        with pytest.raises(RootNotFoundError):
            svc.resolve_for_instance("tmp-images", "z" * 32)
        # 31 chars — too short.
        with pytest.raises(RootNotFoundError):
            svc.resolve_for_instance("tmp-images", "a" * 31)
        # 33 chars — too long.
        with pytest.raises(RootNotFoundError):
            svc.resolve_for_instance("tmp-images", "a" * 33)

    def test_unknown_id_returns_root_not_found(
        self, tmp_path: pathlib.Path
    ):
        store = TmpImageStore(tmp_path, max_bytes=10 * 1024 * 1024)
        store.init()
        svc = self._service_with_store(store)
        with pytest.raises(RootNotFoundError):
            svc.resolve_for_instance("tmp-images", "a" * 32)

    def test_torn_sidecar_returns_root_not_found(
        self, tmp_path: pathlib.Path
    ):
        # Save a real entry, then delete the sidecar to simulate
        # a torn-sidecar race (the design the original /api/tmp_images
        # router was hardened against).
        store = TmpImageStore(tmp_path, max_bytes=10 * 1024 * 1024)
        store.init()
        rec = store.save(
            image_id="fedcba9876543210fedcba9876543210",
            content_bytes=b"Hello",
            content_type="text/plain",
        )
        # Delete the sidecar only.
        sidecar = store.dir / f"{rec.image_id}.json"
        sidecar.unlink()
        svc = self._service_with_store(store)
        # The store raises on the open — the service collapses
        # to a uniform 404 (RootNotFoundError envelope).
        with pytest.raises(RootNotFoundError):
            svc.resolve_for_instance("tmp-images", rec.image_id)

    def test_no_store_returns_root_not_found(self):
        # The lifespan failed to wire the store (unwritable data
        # dir, etc.) — the service is constructed with
        # tmp_image_store=None. The root returns the uniform 404.
        cfg = LiveViewsConfig()
        cfg.roots["tmp-images"] = LiveViewsRootConfig(type="tmp_images")
        svc = LiveViewsService(config=cfg, tmp_image_store=None)
        with pytest.raises(RootNotFoundError):
            svc.resolve_for_instance("tmp-images", "a" * 32)


# ===========================================================================
# Group 9 — URL minting
# ===========================================================================


class TestUrlMinting:
    """``LiveViewsService.build_url`` — the same minting the
    ``view_link`` tool wraps. Path-relative by default;
    fully-qualified when ``external_base_url`` is set.
    """

    def test_path_relative_when_no_base(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(type="filesystem", path=str(tmp_path))
        svc = LiveViewsService(config=cfg)
        url = svc.build_url("docs", "foo/bar.html")
        assert url == "/views/docs/foo/bar.html"

    def test_fully_qualified_when_base_set(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig(external_base_url="https://ensemble.example.com")
        cfg.roots["docs"] = LiveViewsRootConfig(type="filesystem", path=str(tmp_path))
        svc = LiveViewsService(config=cfg)
        url = svc.build_url("docs", "foo/bar.html")
        assert url == "https://ensemble.example.com/views/docs/foo/bar.html"

    def test_base_trailing_slash_stripped(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig(external_base_url="https://ensemble.example.com/")
        cfg.roots["docs"] = LiveViewsRootConfig(type="filesystem", path=str(tmp_path))
        svc = LiveViewsService(config=cfg)
        url = svc.build_url("docs", "foo/bar.html")
        assert url == "https://ensemble.example.com/views/docs/foo/bar.html"
        # No double-slash in the path.
        assert "//views" not in url

    def test_unknown_root_returns_none(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(type="filesystem", path=str(tmp_path))
        svc = LiveViewsService(config=cfg)
        assert svc.build_url("unknown", "foo") is None

    def test_disabled_root_returns_none(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig()
        cfg.roots["off"] = LiveViewsRootConfig(
            type="filesystem", path=str(tmp_path), enabled=False
        )
        svc = LiveViewsService(config=cfg)
        assert svc.build_url("off", "foo") is None

    def test_subsystem_disabled_returns_none(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig(enabled=False)
        cfg.roots["docs"] = LiveViewsRootConfig(type="filesystem", path=str(tmp_path))
        svc = LiveViewsService(config=cfg)
        assert svc.build_url("docs", "foo") is None

    def test_malformed_root_name_returns_none(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(type="filesystem", path=str(tmp_path))
        svc = LiveViewsService(config=cfg)
        # The tool's typing rejects non-string / non-kebab-case
        # names; the service does too (defense in depth).
        assert svc.build_url("", "foo") is None
        assert svc.build_url("Has_Caps", "foo") is None

    def test_malformed_path_returns_none(self, tmp_path: pathlib.Path):
        cfg = LiveViewsConfig()
        cfg.roots["docs"] = LiveViewsRootConfig(type="filesystem", path=str(tmp_path))
        svc = LiveViewsService(config=cfg)
        # Traversal shape — uniform rejection.
        assert svc.build_url("docs", "../etc/passwd") is None
        assert svc.build_url("docs", "/leading") is None


# ===========================================================================
# Group 10 — project_scoped root (planning)
# ===========================================================================


class TestProjectScopedRoot:
    """The ``project_scoped`` root type — URL is
    ``/views/<root>/<project_shortname>/<rel>``; the
    shortname is the FIRST URL segment.
    """

    def _service_with_shortname_resolver(
        self,
        shortname_map: dict[str, str | None],
        root_path: str,
    ) -> LiveViewsService:
        cfg = LiveViewsConfig()
        cfg.roots["planning"] = LiveViewsRootConfig(
            type="project_scoped", path=root_path
        )

        def resolver(shortname: str) -> str | None:
            return shortname_map.get(shortname)

        return LiveViewsService(
            config=cfg,
            project_workdir_by_shortname_resolver=resolver,
        )

    def test_shortname_resolves_to_project_workdir(
        self, tmp_path: pathlib.Path
    ):
        workdir = tmp_path / "ens"
        workdir.mkdir()
        (workdir / ".agents" / "shared" / "planning").mkdir(parents=True)
        (workdir / ".agents" / "shared" / "planning" / "spec.md").write_text(
            "hi"
        )
        svc = self._service_with_shortname_resolver(
            {"ens": str(workdir)},
            ".agents/shared/planning",
        )
        resolved = svc.resolve_for_instance(
            "planning", "ens/spec.md"
        )
        assert (
            resolved.on_disk_path
            == workdir / ".agents" / "shared" / "planning" / "spec.md"
        )

    def test_unknown_shortname_returns_root_not_found(
        self, tmp_path: pathlib.Path
    ):
        svc = self._service_with_shortname_resolver(
            {"ens": str(tmp_path)},
            ".agents/shared/planning",
        )
        with pytest.raises(RootNotFoundError):
            svc.resolve_for_instance("planning", "other/spec.md")

    def test_no_resolver_returns_root_not_found(self):
        # Lifespan forgot to wire the shortname resolver — the
        # root is uniform 404 (never a 500 / never a crash).
        cfg = LiveViewsConfig()
        cfg.roots["planning"] = LiveViewsRootConfig(
            type="project_scoped", path=".agents/shared/planning"
        )
        svc = LiveViewsService(config=cfg)
        with pytest.raises(RootNotFoundError):
            svc.resolve_for_instance("planning", "ens/spec.md")

    def test_only_shortname_no_rel_returns_root_not_found(
        self, tmp_path: pathlib.Path
    ):
        # URL is just ``/views/planning/<shortname>`` with no
        # following path — uniform 404.
        svc = self._service_with_shortname_resolver(
            {"ens": str(tmp_path)},
            ".agents/shared/planning",
        )
        with pytest.raises(RootNotFoundError):
            svc.resolve_for_instance("planning", "ens")

    def test_traversal_in_sub_rel_rejected(self, tmp_path: pathlib.Path):
        # The sub_rel is the path AFTER the shortname. A
        # traversal shape there must be rejected (uniform 404).
        svc = self._service_with_shortname_resolver(
            {"ens": str(tmp_path)},
            ".agents/shared/planning",
        )
        with pytest.raises(TraversalError):
            svc.resolve_for_instance("planning", "ens/../../etc/passwd")


class TestProjectScopedMockupsSubtreeGate:
    """REWORK 2026-10-07 (M2+M3): ``designer-artifact`` is a
    project_scoped root with the canonical mockups subtree
    prefix enforced structurally via the new
    ``required_rel_subpath`` config field.

    These tests pin the M2+M3 closure:
    * the rel's parts must contain ``['design', 'mockups']`` as
      a contiguous subsequence (M3 structural enforcement);
    * the URL pattern ``<shortname>/<feature>/design/mockups/<file>``
      resolves; arbitrary rels (e.g. ``<shortname>/spec.md``) do
      not — the M3 closure in action.
    """

    def _service(
        self,
        shortname_map: dict[str, str | None],
        required: list[str] | None,
    ) -> LiveViewsService:
        cfg = LiveViewsConfig()
        cfg.roots["designer-artifact"] = LiveViewsRootConfig(
            type="project_scoped",
            path=".agents/shared/planning",
            required_rel_subpath=required,
        )

        def resolver(shortname: str) -> str | None:
            return shortname_map.get(shortname)

        return LiveViewsService(
            config=cfg,
            project_workdir_by_shortname_resolver=resolver,
        )

    def test_happy_path_serves_under_mockups_subtree(
        self, tmp_path: pathlib.Path
    ):
        workdir = tmp_path / "ens"
        workdir.mkdir()
        mockup = (
            workdir
            / ".agents"
            / "shared"
            / "planning"
            / "feat"
            / "design"
            / "mockups"
        )
        mockup.mkdir(parents=True)
        (mockup / "landing.html").write_text("<h1>OK</h1>")
        svc = self._service(
            {"ens": str(workdir)},
            ["design", "mockups"],
        )
        resolved = svc.resolve_for_instance(
            "designer-artifact", "ens/feat/design/mockups/landing.html"
        )
        assert (
            resolved.on_disk_path
            == workdir
            / ".agents"
            / "shared"
            / "planning"
            / "feat"
            / "design"
            / "mockups"
            / "landing.html"
        )

    def test_rel_outside_mockups_subtree_rejected(self, tmp_path: pathlib.Path):
        workdir = tmp_path / "ens"
        workdir.mkdir()
        # A planning file OUTSIDE the mockups subtree — this
        # is what M3 must NOT serve. The file exists; the
        # M3 gate refuses the rel shape.
        planning = workdir / ".agents" / "shared" / "planning"
        planning.mkdir(parents=True)
        (planning / "spec.md").write_text("LEAK")
        svc = self._service(
            {"ens": str(workdir)},
            ["design", "mockups"],
        )
        with pytest.raises(RootNotFoundError):
            svc.resolve_for_instance(
                "designer-artifact", "ens/spec.md"
            )

    def test_rel_missing_contiguous_subpath_rejected(
        self, tmp_path: pathlib.Path
    ):
        # A rel that contains 'design' but NOT contiguously
        # followed by 'mockups' (e.g. 'design-foo/mockups/...')
        # must be rejected — the structural gate requires
        # CONTIGUITY, not just substring containment.
        workdir = tmp_path / "ens"
        workdir.mkdir()
        (workdir / ".agents" / "shared" / "planning").mkdir(parents=True)
        svc = self._service(
            {"ens": str(workdir)},
            ["design", "mockups"],
        )
        with pytest.raises(RootNotFoundError):
            svc.resolve_for_instance(
                "designer-artifact", "ens/design-foo/mockups/landing.html"
            )

    def test_no_required_rel_subpath_means_no_gate(self, tmp_path: pathlib.Path):
        # An operator-supplied project_scoped root WITHOUT a
        # required_rel_subpath does NOT enforce the mockups
        # gate (the field is optional; absent = no gate).
        workdir = tmp_path / "ens"
        workdir.mkdir()
        (workdir / ".agents" / "shared" / "planning").mkdir(parents=True)
        (workdir / ".agents" / "shared" / "planning" / "spec.md").write_text(
            "hi"
        )
        svc = self._service({"ens": str(workdir)}, None)
        resolved = svc.resolve_for_instance(
            "designer-artifact", "ens/spec.md"
        )
        assert resolved.on_disk_path == (
            workdir / ".agents" / "shared" / "planning" / "spec.md"
        )


class TestResolveOsErrorToUniform404:
    """HARDENING (M10): ``Path.resolve()`` can raise OSError
    (EACCES on a parent dir, ELOOP on a symlink loop the
    shape-check didn't catch, ENOTCONN / EIO on network FS).
    The router only catches the three typed errors
    (RootNotFoundError, TraversalError, PathNotFoundError);
    a leaked OSError surfaces as a FastAPI 500 — violating
    the uniform-404 / no-disclosure contract.

    The M10 service-side fix collapses OSError on the two
    ``Path.resolve()`` calls in ``_resolve_under_root`` into
    ``RootNotFoundError`` (chained ``from exc`` so the
    daemon log still carries the errno). The router's
    three-error catch then collapses to the uniform 404.

    These tests pin the contract: any OSError-raising
    resolve must surface as ``RootNotFoundError`` (NOT a
    ``OSError`` leak), and the chained ``__cause__`` is the
    original exception.
    """

    @staticmethod
    def _service(tmp_path: pathlib.Path) -> LiveViewsService:
        from daemon.config import LiveViewsConfig, LiveViewsRootConfig
        from daemon.services.live_views import LiveViewsService

        cfg = LiveViewsConfig(
            enabled=True,
            roots={
                "fs-root": LiveViewsRootConfig(
                    type="filesystem",
                    path=str(tmp_path / "root"),
                ),
            },
        )
        (tmp_path / "root").mkdir()
        (tmp_path / "root" / "ok.txt").write_text("hi")
        return LiveViewsService(
            config=cfg,
            tmp_image_store=None,
        )

    def test_oserror_on_resolve_collapses_to_root_not_found(
        self, tmp_path: pathlib.Path, monkeypatch
    ):
        from daemon.services.live_views import RootNotFoundError

        svc = self._service(tmp_path)
        # Patch ``Path.resolve`` to raise PermissionError (an
        # OSError subclass) on the root_dir resolution. The
        # service must catch it and raise RootNotFoundError.
        real_resolve = pathlib.Path.resolve

        def boom(self, *args, **kwargs):
            # Raise on the FIRST resolve (the root_dir
            # resolution). Subsequent resolves (the
            # candidate path) are unmocked.
            if str(self).endswith("/root") or str(self) == str(
                tmp_path / "root"
            ):
                raise PermissionError(
                    errno.EACCES, "Permission denied (test stub)"
                )
            return real_resolve(self, *args, **kwargs)

        monkeypatch.setattr(pathlib.Path, "resolve", boom)
        with pytest.raises(RootNotFoundError) as excinfo:
            svc.resolve_for_instance("fs-root", "ok.txt")
        # The chained ``__cause__`` carries the original
        # OSError for the daemon log.
        assert isinstance(excinfo.value.__cause__, PermissionError)

    def test_oserror_on_candidate_resolve_collapses_to_root_not_found(
        self, tmp_path: pathlib.Path, monkeypatch
    ):
        from daemon.services.live_views import RootNotFoundError

        svc = self._service(tmp_path)
        real_resolve = pathlib.Path.resolve
        first_call = {"count": 0}

        def boom(self, *args, **kwargs):
            first_call["count"] += 1
            # Raise on the SECOND resolve (the candidate
            # path resolution), not the root_dir.
            if first_call["count"] == 2:
                raise OSError(errno.EACCES, "Permission denied (test stub)")
            return real_resolve(self, *args, **kwargs)

        monkeypatch.setattr(pathlib.Path, "resolve", boom)
        with pytest.raises(RootNotFoundError) as excinfo:
            svc.resolve_for_instance("fs-root", "ok.txt")
        assert isinstance(excinfo.value.__cause__, OSError)
