"""Unit tests for ``daemon.services.workspace_guard.WorkspaceGuard``.

Covers the path-traversal protection / boundary-checking logic used by the
workspace viewer routers. These tests are pure unit tests — no database, no
HTTP client, no async — and run quickly under SQLite-free pytest sessions.

Run only this file::

    python -m pytest tests/test_workspace_guard.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# Ensure ``daemon.services`` is importable. The repo uses ``daemon.*`` import
# paths rooted at the project root; pytest's ``rootdir`` puts us there already.
from daemon.services.workspace_guard import WorkspaceGuard


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def workdir():
    """Create a scratch workdir with a known tree layout.

    Layout::

        workdir/
            file.txt
            nested/
                deep.txt
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "file.txt").write_text("hello\n", encoding="utf-8")
        nested = root / "nested"
        nested.mkdir()
        (nested / "deep.txt").write_text("deep\n", encoding="utf-8")
        yield root


@pytest.fixture
def outside_dir():
    """A second tempdir guaranteed to be outside ``workdir`` (string-wise).

    NOTE: On macOS, ``TemporaryDirectory`` resolves through
    ``/var/folders/.../T/`` whose canonical path is ``/private/tmp``, and the
    WorkspaceGuard explicitly allows any target under ``tempfile.gettempdir()``,
    ``/tmp``, ``/private/tmp``, or ``/var/tmp``. Tests that need a genuine
    "outside" target must monkeypatch the guard's ``_is_in_temp_dir`` — see
    the ``disable_tempdir_allowance`` fixture below.
    """
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def disable_tempdir_allowance(monkeypatch):
    """Force the temp-dir allowance off for the duration of one test.

    The WorkspaceGuard intentionally treats ``/tmp`` (and friends) as
    "within" for legitimate tooling usage. Tests that want to assert
    strict boundary rejection use this fixture so the only thing left is
    the canonical ``base/target`` membership check.
    """
    monkeypatch.setattr(
        WorkspaceGuard, "_is_in_temp_dir", staticmethod(lambda _: False)
    )


# ──────────────────────────────────────────────────────────────────────────────
# Construction / validation
# ──────────────────────────────────────────────────────────────────────────────


class TestConstruction:
    def test_resolves_existing_workdir(self, workdir):
        """Existing directories canonicalize without raising."""
        guard = WorkspaceGuard(str(workdir))
        assert guard.workdir == workdir.resolve()
        assert guard.workdir.is_dir()

    def test_expands_user_in_workdir(self, workdir, monkeypatch):
        """``~`` in the workdir argument expands via ``expanduser``.

        With ``HOME`` pointing at the workdir's parent, ``~/foo`` resolves to
        ``parent / foo``. The constructor must accept an existing expansion
        and reject a non-existent one.
        """
        sentinel = workdir.parent
        monkeypatch.setenv("HOME", str(sentinel))
        target = sentinel / "workspace_test_workdir"
        target.mkdir()
        try:
            guard = WorkspaceGuard("~/workspace_test_workdir")
            assert guard.workdir == target.resolve()
        finally:
            target.rmdir()

        # Non-existent expansion must raise ValueError with the original arg.
        with pytest.raises(ValueError, match="does not exist"):
            WorkspaceGuard("~/definitely_missing_after_expand")

    def test_nonexistent_workdir_raises_value_error(self):
        """Passing a path that doesn't exist raises ``ValueError``."""
        with pytest.raises(ValueError, match="does not exist"):
            WorkspaceGuard("/this/path/does/not/exist/anywhere_xyz")

    def test_class_constants_sane(self):
        """Pin the published limits and ignore set used by HTTP routers.

        The 1 MiB ceiling and the ignore-set are consumed by the workspace
        viewer endpoints; an accidental flip would silently change HTTP
        behaviour, so we pin them here.
        """
        assert WorkspaceGuard.MAX_FILE_SIZE_BYTES == 1_048_576  # 1 MiB
        assert isinstance(WorkspaceGuard.DEFAULT_TREE_DEPTH, int)
        assert WorkspaceGuard.DEFAULT_TREE_DEPTH >= 1
        expected_must_include = {
            ".git", "node_modules", "__pycache__", ".venv",
        }
        assert expected_must_include.issubset(WorkspaceGuard.IGNORE_PATTERNS)


# ──────────────────────────────────────────────────────────────────────────────
# ``resolve()`` — happy paths
# ──────────────────────────────────────────────────────────────────────────────


class TestResolveHappyPath:
    def test_resolves_simple_relative_path(self, workdir):
        """A plain relative path resolves against the workdir."""
        guard = WorkspaceGuard(str(workdir))
        resolved, err = guard.resolve("file.txt")
        assert err is None
        assert resolved is not None
        assert resolved == (workdir / "file.txt").resolve()

    def test_resolves_nested_relative_path(self, workdir):
        """Nested relative paths are joined onto the workdir."""
        guard = WorkspaceGuard(str(workdir))
        resolved, err = guard.resolve("nested/deep.txt")
        assert err is None
        assert resolved is not None
        assert resolved == (workdir / "nested" / "deep.txt").resolve()

    def test_resolves_dot_path(self, workdir):
        """``./file.txt`` canonicalizes to the same target."""
        guard = WorkspaceGuard(str(workdir))
        resolved, err = guard.resolve("./file.txt")
        assert err is None
        assert resolved is not None
        assert resolved == (workdir / "file.txt").resolve()

    def test_dot_resolves_to_workdir_itself(self, workdir):
        """``resolve(".")`` returns the canonicalised workdir."""
        guard = WorkspaceGuard(str(workdir))
        resolved, err = guard.resolve(".")
        assert err is None
        assert resolved is not None
        assert resolved == guard.workdir

    def test_absolute_path_within_workdir_passes_through(self, workdir):
        """An absolute path that lives inside the workdir passes through.

        The contract: absolute paths skip the boundary check, so even paths
        that happen to land inside workdir are returned unchanged. This test
        pins that behaviour.
        """
        guard = WorkspaceGuard(str(workdir))
        abs_target = str((workdir / "nested" / "deep.txt").resolve())
        resolved, err = guard.resolve(abs_target)
        assert err is None
        assert resolved is not None
        assert Path(str(resolved)).resolve() == (workdir / "nested" / "deep.txt").resolve()

    def test_resolve_allows_temp_dir(self, workdir):
        """``resolve()`` (agent tools) should still allow temp dir access."""
        guard = WorkspaceGuard(str(workdir))
        tmp_file = os.path.join(
            tempfile.gettempdir(), "test_workspace_guard_temp.txt"
        )
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                f.write("test")

            resolved, err = guard.resolve(tmp_file)

            assert resolved is not None
            assert err is None
        finally:
            if os.path.exists(tmp_file):
                os.unlink(tmp_file)


# ──────────────────────────────────────────────────────────────────────────────
# ``resolve()`` — security / boundary failure paths
# ──────────────────────────────────────────────────────────────────────────────


class TestPathTraversal:
    def test_dotdot_traversal_rejected(self, workdir, outside_dir, disable_tempdir_allowance):
        """A path that climbs out via ``..`` is rejected with an error.

        The tempdir exemption is disabled so the only thing left is the
        canonical ``base/target`` membership check.
        """
        bad = "../" + os.path.basename(str(outside_dir)) + "/secret.txt"
        guard = WorkspaceGuard(str(workdir))
        resolved, err = guard.resolve(bad)
        assert resolved is None
        assert err is not None
        assert "escapes workdir" in err or "outside" in err.lower()

    def test_etc_passwd_traversal_rejected(self, workdir):
        """The classic ``../../../etc/passwd`` traversal is rejected.

        This intentionally uses the production temp-directory rules rather
        than the strict-boundary fixture: the traversal resolves outside both
        the workdir and the allowed temp tree on supported POSIX hosts.
        """
        guard = WorkspaceGuard(str(workdir))
        resolved, err = guard.resolve("../../../etc/passwd")
        assert resolved is None
        assert err is not None
        assert "escapes workdir" in err

    def test_symlink_pointing_outside_rejected(
        self, workdir, outside_dir, disable_tempdir_allowance,
    ):
        """A symlink inside workdir that points outside is rejected.

        ``Path.resolve()`` follows symlinks, so the canonicalised target
        lands under the sibling tempdir. With the tempdir exemption off,
        that canonicalised target fails the boundary check.
        """
        outside_file = outside_dir / "secret.txt"
        outside_file.write_text("top secret", encoding="utf-8")

        link = workdir / "nested" / "link_outside"
        try:
            link.symlink_to(outside_file)
            guard = WorkspaceGuard(str(workdir))
            resolved, err = guard.resolve("nested/link_outside")
            assert resolved is None
            assert err is not None
            assert "escapes workdir" in err
        finally:
            link.unlink(missing_ok=True)

    def test_absolute_path_outside_workdir_bypasses_boundary_check(
        self, workdir, outside_dir,
    ):
        """Absolute paths intentionally bypass the boundary check.

        This mirrors the original filesystem-tools semantics where absolute
        paths are trusted. The guard still returns a usable ``Path`` but
        does not raise — callers (the HTTP routers) rely on this for
        legitimate paths outside the workdir (e.g. ``/tmp`` tooling).
        """
        guard = WorkspaceGuard(str(workdir))
        sentinel = outside_dir / "anywhere.txt"
        sentinel.write_text("hi", encoding="utf-8")
        try:
            resolved, err = guard.resolve(str(sentinel))
            assert err is None
            assert resolved is not None
            assert Path(str(resolved)).resolve() == sentinel.resolve()
        finally:
            sentinel.unlink(missing_ok=True)

    def test_symlink_within_workdir_is_resolved_inside(self, workdir):
        """An internal symlink (pointing inside workdir) is allowed through."""
        (workdir / "file.txt").rename(workdir / "file_target.txt")
        (workdir / "file.txt").symlink_to(workdir / "file_target.txt")

        guard = WorkspaceGuard(str(workdir))
        resolved, err = guard.resolve("file.txt")
        assert err is None
        assert resolved is not None
        # ``Path.resolve()`` follows the symlink; both aliases end up the same.
        assert resolved == (workdir / "file_target.txt").resolve()

    def test_relative_path_outside_workdir_but_in_tmpdir_allowed(
        self, workdir,
    ):
        """A relative traversal that lands under the tempdir is allowed.

        The workdir itself lives under ``tempfile.gettempdir()`` (every
        ``TemporaryDirectory`` does on POSIX), so ``../<sibling>.txt``
        canonicalises to a sibling inside the tempdir — which the tempdir
        allowance permits. This pins the "legitimate tooling usage" carve
        out.
        """
        guard = WorkspaceGuard(str(workdir))
        # A sibling inside the tempdir but outside the workdir.
        sibling = workdir.parent / "wg_sibling_in_tmpdir.txt"
        sibling.write_text("ok", encoding="utf-8")
        try:
            rel = "../" + sibling.name
            resolved, err = guard.resolve(rel)
            assert err is None
            assert resolved is not None
            assert Path(str(resolved)).resolve() == sibling.resolve()
        finally:
            sibling.unlink(missing_ok=True)


# ──────────────────────────────────────────────────────────────────────────────
# ``resolve_strict()`` — HTTP-only boundary behavior
# ──────────────────────────────────────────────────────────────────────────────


class TestResolveStrict:
    def test_resolve_strict_rejects_absolute_outside_workdir(self, workdir):
        """``resolve_strict`` must reject absolute paths outside workdir."""
        guard = WorkspaceGuard(str(workdir))

        resolved, err = guard.resolve_strict("/etc/passwd")

        assert resolved is None
        assert err is not None
        assert "escapes workdir" in err

    def test_resolve_strict_rejects_temp_dir(self, workdir):
        """``resolve_strict`` must not exempt temp dirs (unlike ``resolve``)."""
        guard = WorkspaceGuard(str(workdir))

        resolved, err = guard.resolve_strict("/tmp/secret.txt")

        assert resolved is None
        assert err is not None

    def test_resolve_strict_rejects_null_bytes(self, workdir):
        """``resolve_strict`` handles null bytes gracefully rather than raising."""
        guard = WorkspaceGuard(str(workdir))

        resolved, err = guard.resolve_strict("foo\x00bar")

        assert resolved is None
        assert err is not None


# ──────────────────────────────────────────────────────────────────────────────
# Absolute-path detection (POSIX + mocked Windows)
# ──────────────────────────────────────────────────────────────────────────────


class TestAbsolutePathDetection:
    @pytest.mark.parametrize(
        "path,expected",
        [
            ("relative/file.txt", False),
            ("", False),
            ("   ", False),
            ("/etc/passwd", True),
            ("/tmp/foo", True),
            ("./foo", False),
            ("../foo", False),
        ],
    )
    def test_unix_absolute_paths(self, path, expected):
        """OS-appropriate detection on the live host (darwin / linux)."""
        result = WorkspaceGuard._is_absolute_path(path)
        assert result is expected, f"{path!r}: expected {expected}, got {result}"

    def test_windows_drive_letter_backslashes_mocked(self):
        """Windows-style ``C:\\\\foo`` is detected even on POSIX test hosts.

        The regex branch only fires when ``Path.is_absolute()`` returns False
        — on POSIX, ``C:\\foo`` is NOT absolute. We simulate the Windows
        branch by patching ``Path.is_absolute`` to return False and
        confirming the drive-letter regex matches anyway.
        """
        with patch.object(Path, "is_absolute", return_value=False):
            assert WorkspaceGuard._is_absolute_path("C:\\Users\\admin") is True

    def test_windows_drive_letter_forward_slash_mocked(self):
        """``D:/work/file`` (forward slash drive) is detected via regex."""
        with patch.object(Path, "is_absolute", return_value=False):
            assert WorkspaceGuard._is_absolute_path("D:/work/file") is True

    def test_windows_unc_path_mocked(self):
        """UNC ``\\\\server\\share`` is detected via the UNC regex on POSIX hosts."""
        with patch.object(Path, "is_absolute", return_value=False):
            assert WorkspaceGuard._is_absolute_path("\\\\server\\share") is True

    def test_lowercase_drive_letter_mocked(self):
        """Drive-letter regex is case-insensitive on the letter."""
        with patch.object(Path, "is_absolute", return_value=False):
            assert WorkspaceGuard._is_absolute_path("c:/work") is True
            assert WorkspaceGuard._is_absolute_path("z:\\share") is True


# ──────────────────────────────────────────────────────────────────────────────
# Empty / whitespace / invalid input
# ──────────────────────────────────────────────────────────────────────────────


class TestInvalidInput:
    def test_empty_string_returns_error(self, workdir):
        """Empty relative path returns an error rather than resolving to workdir."""
        guard = WorkspaceGuard(str(workdir))
        resolved, err = guard.resolve("")
        assert resolved is None
        assert err is not None
        assert "workdir is required" in err or "invalid" in err.lower()

    def test_whitespace_only_path_returns_error(self, workdir):
        """Whitespace-only paths raise an error instead of leaking."""
        guard = WorkspaceGuard(str(workdir))
        resolved, err = guard.resolve("   ")
        assert resolved is None
        assert err is not None

    def test_traversal_to_sibling_returns_error_when_strict(
        self, workdir, outside_dir, disable_tempdir_allowance,
    ):
        """Plain ``../<sibling>`` is rejected with strict boundary checking.

        Mirrors the production guard's ``_resolve_target`` exit: when the
        canonicalised target is outside the workdir AND not under an
        allowed tempdir, ``resolve()`` returns ``(None, "escapes workdir")``.
        """
        guard = WorkspaceGuard(str(workdir))
        bad = "../" + os.path.basename(str(outside_dir))
        resolved, err = guard.resolve(bad)
        assert resolved is None
        assert err is not None
        assert "escapes workdir" in err


# ──────────────────────────────────────────────────────────────────────────────
# ``is_within()`` — explicit boundary query
# ──────────────────────────────────────────────────────────────────────────────


class TestIsWithin:
    def test_path_inside_workdir(self, workdir):
        """A path inside workdir is reported as within."""
        guard = WorkspaceGuard(str(workdir))
        target = (workdir / "nested" / "deep.txt").resolve()
        assert guard.is_within(target) is True

    def test_path_outside_workdir_returns_false_when_strict(
        self, workdir, outside_dir, disable_tempdir_allowance,
    ):
        """A sibling tempdir is *not* within once the tempdir exemption is off."""
        guard = WorkspaceGuard(str(workdir))
        assert guard.is_within(outside_dir) is False

    def test_file_at_workdir_root(self, workdir):
        """The workdir root itself is within."""
        guard = WorkspaceGuard(str(workdir))
        assert guard.is_within(workdir.resolve()) is True

    def test_path_inside_tmpdir_is_within_per_temp_rule(self, workdir):
        """Files inside ``tempfile.gettempdir()`` are considered within.

        The guard allows ``/tmp`` for legitimate tooling usage. The path
        does not have to exist — the boundary check operates on the path
        string, not the file.
        """
        guard = WorkspaceGuard(str(workdir))
        some_temp_file = Path(tempfile.gettempdir()) / "definitely_not_inside_workdir.txt"
        assert guard.is_within(some_temp_file) is True


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────────────────


class TestNormedContains:
    """Direct coverage of the static ``_normed_contains`` helper."""

    def test_target_inside_base(self, workdir):
        base = workdir.resolve()
        target = (workdir / "nested" / "deep.txt").resolve()
        assert WorkspaceGuard._normed_contains(base, target) is True

    def test_target_outside_base(self, workdir, outside_dir):
        base = workdir.resolve()
        target = outside_dir.resolve()
        assert WorkspaceGuard._normed_contains(base, target) is False

    def test_target_equals_base(self, workdir):
        """The base itself is considered to contain itself."""
        base = workdir.resolve()
        assert WorkspaceGuard._normed_contains(base, base) is True

    def test_sibling_with_string_prefix_is_not_within(self, workdir):
        """Siblings that share a string prefix are NOT considered within.

        ``relative_to`` is the contract — not naive ``startswith`` checks —
        so a sibling named ``workdir + "_sentinel"`` must be rejected.
        """
        base = workdir.resolve()
        sibling = workdir.parent / (workdir.name + "_sentinel")
        if sibling.exists():
            pytest.skip("Sibling sentinel already exists on disk.")
        sibling.mkdir()
        try:
            assert WorkspaceGuard._normed_contains(base, sibling) is False
        finally:
            sibling.rmdir()


class TestIsInTempDir:
    """Direct coverage of the ``_is_in_temp_dir`` classmethod."""

    def test_file_under_gettempdir_is_in_temp_dir(self):
        target = Path(tempfile.gettempdir()) / "ensemble_wg_sentinel.txt"
        assert WorkspaceGuard._is_in_temp_dir(target) is True

    def test_path_outside_all_temp_dirs_is_not_in_temp_dir(self):
        """``/etc`` is not a tempdir on any supported platform."""
        assert WorkspaceGuard._is_in_temp_dir(Path("/etc/passwd")) is False


# ──────────────────────────────────────────────────────────────────────────────
# Ignore-pattern constants
# ──────────────────────────────────────────────────────────────────────────────


class TestIgnorePatterns:
    def test_ignore_set_is_frozen(self):
        """``IGNORE_PATTERNS`` is immutable — router code mutates a copy."""
        patterns = WorkspaceGuard.IGNORE_PATTERNS
        assert isinstance(patterns, frozenset)

    def test_ignore_set_iterable_and_nonempty(self):
        patterns = WorkspaceGuard.IGNORE_PATTERNS
        entries = list(patterns)
        assert len(entries) > 0
        # Membership check works.
        assert ".git" in patterns
        assert "node_modules" in patterns

    def test_ignore_set_contains_documented_entries(self):
        """Every documented ignore name must be present."""
        documented = {
            ".git", "node_modules", "__pycache__", ".venv", "venv",
            "dist", "build", ".next", ".pytest_cache", ".mypy_cache",
            ".tox", "egg-info", ".eggs",
        }
        missing = documented - WorkspaceGuard.IGNORE_PATTERNS
        assert not missing, f"Missing from IGNORE_PATTERNS: {missing}"

    def test_ignore_set_does_not_swallow_user_code(self):
        """Regression guard: user-visible dirs must not be silently ignored."""
        for must_not_include in {"src", "lib", "app", "tests", "docs"}:
            assert must_not_include not in WorkspaceGuard.IGNORE_PATTERNS


# ──────────────────────────────────────────────────────────────────────────────
# POSIX smoke test
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX-specific smoke test")
def test_guard_can_build_against_tmpdir_sentinel(workdir):
    """Constructing against a fresh tempdir works on POSIX hosts (basic smoke)."""
    g = WorkspaceGuard(str(workdir))
    assert g.workdir.exists()
    # Resolving ``.`` yields the workdir itself.
    resolved, err = g.resolve(".")
    assert err is None
    assert resolved is not None
    assert resolved == g.workdir